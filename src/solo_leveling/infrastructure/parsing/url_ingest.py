"""
infrastructure/parsing/url_ingest — URL 등록(M1 기능 ①의 URL 부분)

PDF 링크면 기존 PDF 파일과 동일하게 처리하고, 일반 웹페이지면 본문 텍스트를
추출해 "페이지 1개짜리 논문"처럼 취급한다.

보안 주의(팀 공유): 여기서는 사설망 접근을 막는 최소한의 검증만 있었다.
04번 문서에서 "URL 수집의 사설망 접근 차단"은 C 담당 영역으로 명시되어 있고,
C가 최종 보안 검증 로직과 반드시 통합·재검토해야 한다. 그전까지는
A가 URL 파이프라인을 개발·테스트할 수 있게 하는 임시 최소 방어선이다.

이번 보강 범위: 리다이렉트 매 홉 재검증(최초 URL만 검사하고 끝나면
최종 목적지가 사설망이어도 못 잡는 구멍이 있었음), 응답 크기·전체 다운로드
시간 제한(Content-Length 위조·누락에도 실제 수신 바이트로 강제).
DNS 리바인딩 방어(호스트명 검증 시점과 실제 커넥션 시점의 IP가 다를 수 있는
문제)는 여전히 다루지 않는다 — 이건 C의 정식 네트워크 정책 통합 시 필요.
"""
import ipaddress
import re
import time
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

USER_AGENT = "solo-leveling-paper/0.1 (URL registration)"
REQUEST_TIMEOUT = (5, 30)  # (connect, read) per request
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # 50MB
MAX_TOTAL_SECONDS = 60  # 리다이렉트 포함 전체 다운로드 시간 상한

_PRIVATE_HOSTNAMES = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


class UnsafeUrlError(ValueError):
    """사설망·비HTTP(S) URL 등 등록을 거부해야 하는 경우"""


class ResponseTooLargeError(ValueError):
    """허용된 응답 크기를 초과한 경우"""


def _is_private_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # 호스트네임은 여기서 판단 못 함 — DNS 리바인딩 방어는 C 영역
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def validate_public_url(url: str) -> None:
    """
    사설망·로컬호스트·비HTTP(S) URL을 거부한다.
    주의: 여긴 최소 방어선이며, DNS 리바인딩, 리다이렉트 체인 끝단 확인은
    fetch_url()이 매 홉마다 이 함수를 다시 호출하는 방식으로 보완한다.
    최종 정식 네트워크 정책과의 통합은 C 담당 영역(04번 문서).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"http/https만 허용됩니다: {url}")
    if not parsed.hostname:
        raise UnsafeUrlError(f"호스트를 확인할 수 없습니다: {url}")
    if parsed.hostname.lower() in _PRIVATE_HOSTNAMES:
        raise UnsafeUrlError(f"사설/로컬 주소는 등록할 수 없습니다: {url}")
    if _is_private_ip(parsed.hostname):
        raise UnsafeUrlError(f"사설 IP 대상은 등록할 수 없습니다: {url}")


def _consume_with_limits(response: requests.Response, max_bytes: int, deadline: float) -> bytes:
    """스트리밍으로 읽으며 크기·시간 제한을 강제한다.

    Content-Length 헤더가 없거나 실제 응답보다 작게 위조된 경우에도, 실제로
    수신한 바이트 수를 누적해서 판단하므로 우회되지 않는다.
    """
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise ResponseTooLargeError(
                    f"응답 크기가 허용 범위를 초과합니다(Content-Length={content_length}): {response.url}"
                )
        except ValueError:
            pass  # 헤더 형식이 이상하면 무시하고 실제 크기로 판단

    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        if time.monotonic() > deadline:
            raise TimeoutError(f"응답 다운로드 시간이 허용 범위를 초과했습니다: {response.url}")
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLargeError(
                f"응답 크기가 허용 범위를 초과합니다({total} bytes): {response.url}"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_url(url: str) -> requests.Response:
    """URL을 안전하게 가져온다.

    리다이렉트를 requests의 allow_redirects에 맡기지 않고 직접 한 홉씩 따라간다 —
    그렇지 않으면 최초 URL만 검증하고 최종 목적지(사설망 등)는 그대로 따라가게
    되는 구멍이 생긴다. 응답 본문은 크기·시간 제한을 강제하며 스트리밍으로 읽는다.
    """
    deadline = time.monotonic() + MAX_TOTAL_SECONDS
    current_url = url
    session = requests.Session()
    try:
        for _ in range(MAX_REDIRECTS + 1):
            validate_public_url(current_url)
            response = session.get(
                current_url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise UnsafeUrlError(f"리다이렉트 응답에 Location이 없습니다: {current_url}")
                current_url = urljoin(current_url, location)
                continue

            response.raise_for_status()
            body = _consume_with_limits(response, MAX_RESPONSE_BYTES, deadline)
            # 이미 스트리밍으로 다 읽었으니 response.content/.text가 그 바이트를
            # 재사용하도록 내부 캐시를 채워둔다(다시 네트워크를 읽지 않음).
            response._content = body
            response._content_consumed = True
            return response
    finally:
        session.close()

    raise UnsafeUrlError(f"리다이렉트 횟수가 허용 범위를 초과했습니다({MAX_REDIRECTS}회): {url}")


def classify_content(response: requests.Response, url: str) -> str:
    """반환: "pdf" | "html" | "other" """
    content_type = response.headers.get("Content-Type", "").lower()
    if "application/pdf" in content_type:
        return "pdf"
    if url.lower().endswith(".pdf") and response.content[:4] == b"%PDF":
        return "pdf"
    if response.content[:4] == b"%PDF":
        return "pdf"
    if "text/html" in content_type or "<html" in response.text[:1000].lower():
        return "html"
    return "other"


_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")


def extract_html_text(html: str) -> str:
    """
    아주 단순한 태그 제거 기반 본문 추출.
    정교한 본문 판별(광고·네비게이션 제거 등) 범위 밖. 필요하면
    beautifulsoup4 같은 라이브러리로 교체 가능하도록 이 함수만 바꾸면 된다.
    """
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub("\n", text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n\n", text)
    return text.strip()


def fetch_and_classify(url: str) -> Tuple[str, requests.Response]:
    """URL을 가져와 (kind, response) 반환. kind: "pdf" | "html" | "other" """
    response = fetch_url(url)
    kind = classify_content(response, url)
    return kind, response
