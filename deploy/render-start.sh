#!/bin/sh
set -eu

# Render 무료 Web Service는 pre-deploy command를 지원하지 않는다. 스테이징의
# 단일 인스턴스 시작 전에 advisory lock 기반의 멱등 마이그레이션을 실행한다.
solo-leveling-server-migrate
exec solo-leveling-api
