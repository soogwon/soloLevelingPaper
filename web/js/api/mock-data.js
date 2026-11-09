// 화면 개발용 예시 데이터. 실제 논문 내용·사용자 기록이 아니다.
// 날짜는 오늘 기준으로 만들어서 '오늘', '최근 7일' 필터를 바로 확인할 수 있게 한다.
import { shiftKey, todayKey } from '../lib/format.js';

/** n일 전 한국 시각 HH:MM → ISO(UTC) */
function kst(daysAgo, hhmm) {
  const key = shiftKey(todayKey(), -daysAgo);
  return new Date(`${key}T${hhmm}:00+09:00`).toISOString();
}

const pos = (pdf_page, section_number, section_title, printed_page_label = null) => ({
  pdf_page, printed_page_label, section_number, section_title, chunk_id: `chk_${pdf_page}_${section_number ?? 'x'}`,
});

export function createMockDb() {
  const papers = [
    {
      paper_id: 'pap_attention',
      title: 'Attention Is All You Need',
      authors: ['Ashish Vaswani', 'Noam Shazeer', 'Niki Parmar'],
      published: { year: 2017, precision: 'year' },
      reading_status: 'reading',
      created_at: kst(20, '10:00'),
      version: { version_id: 'ver_attention_1', file_name: 'attention-is-all-you-need.pdf', created_at: kst(20, '10:00'), pdf_status: 'uploaded' },
      record: { learning_record_id: 'rec_attention_1', last_position: pos(5, '3.2', 'Multi-Head Attention', '4'), last_studied_at: kst(0, '14:20') },
      summary: {
        summary_id: 'sum_attention_1',
        created_at: kst(0, '14:30'),
        author_type: 'mcp',
        items: [
          { kind: 'claim', text: '어텐션만으로 입력 간 관계를 학습하는 Transformer 구조를 제안한다.', evidence_ids: ['ev_1', 'ev_2'],
            evidence: [{ evidence_id: 'ev_1', target: 'original', quote: 'Example sentence from the abstract stored as evidence.', position: pos(1, null, 'Abstract') }] },
          { kind: 'method', text: '인코더와 디코더를 셀프 어텐션과 피드포워드 층으로 구성한다.', evidence_ids: ['ev_3'] },
          { kind: 'method', text: '여러 head로 나눈 Multi-Head Attention과 위치 인코딩을 함께 사용한다.', evidence_ids: ['ev_4'] },
          { kind: 'result', text: '번역 실험 결과와 학습 비용을 비교한 내용을 정리했다.', evidence_ids: [] },
        ],
      },
    },
    {
      paper_id: 'pap_bert',
      title: 'BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding',
      authors: ['Jacob Devlin', 'Ming-Wei Chang'],
      published: { year: 2018, precision: 'year' },
      reading_status: 'completed',
      created_at: kst(18, '09:00'),
      version: { version_id: 'ver_bert_1', file_name: 'bert.pdf', created_at: kst(18, '09:00'), pdf_status: 'not_uploaded' },
      record: { learning_record_id: 'rec_bert_1', last_position: pos(9, '5', 'Ablation Studies'), last_studied_at: kst(4, '09:30') },
      summary: {
        summary_id: 'sum_bert_1', created_at: kst(4, '09:40'), author_type: 'mcp',
        items: [{ kind: 'claim', text: '양방향 문맥을 함께 보는 사전학습 방식을 제안한다.', evidence_ids: ['ev_10'] }],
      },
    },
    {
      paper_id: 'pap_vit',
      title: 'An Image is Worth 16x16 Words',
      authors: ['Alexey Dosovitskiy', 'Lucas Beyer'],
      published: { year: 2020, precision: 'year' },
      reading_status: 'reading',
      created_at: kst(12, '20:00'),
      version: { version_id: 'ver_vit_1', file_name: 'vit.pdf', created_at: kst(12, '20:00'), pdf_status: 'uploaded' },
      record: { learning_record_id: 'rec_vit_1', last_position: pos(3, '3.1', 'Vision Transformer (ViT)'), last_studied_at: kst(6, '18:40') },
      summary: null,
    },
    {
      paper_id: 'pap_lora',
      title: 'LoRA: Low-Rank Adaptation of Large Language Models',
      authors: ['Edward J. Hu', 'Yelong Shen'],
      published: { year: 2021, month: 6, precision: 'month' },
      reading_status: 'unknown',
      created_at: kst(40, '11:00'),
      version: { version_id: 'ver_lora_1', file_name: 'lora.pdf', created_at: kst(40, '11:00'), pdf_status: 'uploaded' },
      record: { learning_record_id: 'rec_lora_1', last_position: null, last_studied_at: kst(25, '22:10') },
      summary: null,
    },
    {
      paper_id: 'pap_resnet',
      title: 'Deep Residual Learning for Image Recognition',
      authors: ['Kaiming He', 'Xiangyu Zhang'],
      published: { year: 2015, precision: 'year' },
      reading_status: 'not_started',
      created_at: kst(2, '08:00'),
      version: { version_id: 'ver_resnet_1', file_name: 'resnet.pdf', created_at: kst(2, '08:00'), pdf_status: 'not_uploaded' },
      record: null,
      summary: null,
    },
    {
      paper_id: 'pap_draft',
      title: null,
      authors: [],
      published: null,
      reading_status: 'not_started',
      created_at: kst(1, '23:00'),
      version: { version_id: 'ver_draft_1', file_name: 'graph-rag-survey-draft.pdf', created_at: kst(1, '23:00'), pdf_status: 'not_uploaded' },
      record: null,
      summary: null,
    },
  ];

  // 페이지 이동을 확인하기 위한 예시 논문 18편 (총 24편 → 20개씩 2페이지)
  const cycle = ['reading', 'completed', 'not_started', 'reading', 'unknown'];
  for (let n = 7; n <= 40; n += 1) {
    const nn = String(n).padStart(2, '0');
    const studied = n % 4 !== 0;
    papers.push({
      paper_id: `pap_sample_${nn}`,
      title: `예시 논문 ${nn} · 페이지 확인용`,
      authors: ['Sample Author', 'Second Author'],
      published: { year: 2015 + (n % 9), precision: 'year' },
      reading_status: studied ? cycle[n % cycle.length] : 'not_started',
      created_at: kst(30 + n, '09:00'),
      version: { version_id: `ver_sample_${nn}`, file_name: `sample-${nn}.pdf`, created_at: kst(30 + n, '09:00'), pdf_status: n % 2 ? 'uploaded' : 'not_uploaded' },
      record: studied
        ? { learning_record_id: `rec_sample_${nn}`, last_position: pos((n % 9) + 1, String((n % 5) + 1), 'Sample Section'), last_studied_at: kst(n + 3, '20:00') }
        : null,
      summary: null,
    });
  }

  const seg = (target, quote, position) => ({ target, quote, position });
  const ann = (annotation_id, kind, paperId, created, body, segments, author_type = 'user') => {
    const p = papers.find((x) => x.paper_id === paperId);
    return {
      annotation_id, kind, body, segments,
      paper: { paper_id: p.paper_id, title: p.title ?? p.version.file_name },
      version_id: p.version.version_id,
      learning_record_id: p.record?.learning_record_id ?? null,
      created_at: created,
      updated_at: created ?? kst(20, '12:00'),
      uploaded_at: kst(0, '15:00'),
      author_type,
    };
  };

  const annotations = [
    ann('ann_01', 'note', 'pap_attention', kst(0, '14:20'), '여러 attention head가 서로 다른 역할을 하는지 다시 확인하기.',
      [seg('original', 'Example sentence about attending to several representation subspaces.', pos(5, '3.2', 'Multi-Head Attention'))]),
    ann('ann_02', 'highlight', 'pap_bert', kst(0, '10:35'), '',
      [seg('original', 'Example highlighted sentence from the related work section.', pos(3, '2', 'Related Work'))]),
    ann('ann_03', 'note', 'pap_attention', kst(1, '21:05'), 'Q, K, V 계산 과정을 직접 손으로 정리해보기.', []),
    ann('ann_04', 'highlight', 'pap_vit', kst(1, '18:40'), '패치 크기를 바꾸면 결과가 어떻게 달라지는지 확인.',
      [seg('translation', '이미지를 고정 크기 패치로 나누어 입력한다는 내용의 예시 번역 문장.', pos(3, '3.1', 'Vision Transformer (ViT)'))]),
    ann('ann_05', 'highlight', 'pap_attention', kst(3, '22:12'), '√dₖ로 나누는 이유를 따로 정리하기.', [
      seg('original', 'Example sentence describing the scaling factor.', pos(4, '3.2.1', 'Scaled Dot-Product Attention')),
      seg('translation', '스케일링 계수를 설명하는 예시 번역 문장.', pos(4, '3.2.1', 'Scaled Dot-Product Attention')),
    ]),
    ann('ann_06', 'note', 'pap_bert', kst(4, '09:12'), '다음 문장 예측을 빼면 결과가 어떻게 달라지는지 비교.',
      [seg('translation', '사전학습 과제를 하나씩 제거한 실험의 예시 번역 문장.', pos(9, '5', 'Ablation Studies'))]),
    ann('ann_07', 'highlight', 'pap_attention', kst(9, '16:00'), '',
      [seg('original', 'Example sentence from the introduction.', pos(2, '1', 'Introduction'))]),
    ann('ann_08', 'note', 'pap_lora', null, 'rank를 줄였을 때 성능 차이를 비교해볼 것.',
      [seg('original', 'Example sentence about low-rank update matrices.', pos(6, '4', 'Our Method'))]),
  ];

  // 페이지 이동 확인용 기록: Attention 논문에 8개 더 (상세 패널 여러 페이지), 예시 논문들에 16개
  for (let n = 1; n <= 8; n += 1) {
    annotations.push(ann(`ann_att_${n}`, n % 3 ? 'note' : 'highlight', 'pap_attention', kst(10 + n, `1${n % 10}:00`),
      `예시 메모 ${n} · 상세 화면의 페이지 이동을 확인하기 위한 기록이에요.`,
      n % 3 ? [] : [seg('original', `Example highlighted sentence number ${n}.`, pos(n + 2, '3', 'Model Architecture'))]));
  }
  for (let n = 1; n <= 16; n += 1) {
    const paperId = `pap_sample_${String(7 + (n % 6)).padStart(2, '0')}`;
    annotations.push(ann(`ann_s_${n}`, n % 2 ? 'note' : 'highlight', paperId, kst(11 + n, '19:30'),
      n % 2 ? `예시 메모 ${n} · 메모 페이지 이동을 확인하기 위한 기록이에요.` : '',
      n % 2 ? [] : [seg('translation', `예시 하이라이트 번역 문장 ${n}.`, pos(n, '2', 'Sample Section'))]));
  }

  const collections = [
    { collection_id: 'col_transformer', name: 'Transformer 기초', created_at: kst(10, '10:00'), updated_at: kst(3, '10:00') },
    { collection_id: 'col_lm', name: '언어 모델 연구', created_at: kst(8, '10:00'), updated_at: kst(8, '10:00') },
    { collection_id: 'col_mm', name: '멀티모달 읽을거리', created_at: kst(5, '10:00'), updated_at: kst(5, '10:00') },
    { collection_id: 'col_samples', name: '페이지 확인용 예시', created_at: kst(4, '10:00'), updated_at: kst(4, '10:00') },
  ];

  // 논문 하나는 컬렉션 최대 한 곳 (요청서 5.1) → paper_id 키 하나에 소속 하나
  const membership = new Map([
    ['pap_attention', { collection_id: 'col_transformer', added_at: kst(10, '10:05') }],
    ['pap_bert', { collection_id: 'col_transformer', added_at: kst(9, '10:05') }],
    ['pap_vit', { collection_id: 'col_transformer', added_at: kst(6, '10:05') }],
    ['pap_lora', { collection_id: 'col_lm', added_at: kst(8, '10:05') }],
    // 컬렉션 상세의 페이지 번호 확인용: 예시 논문 07~30 (24편 → 2페이지)
    ...Array.from({ length: 24 }, (_, i) => [
      `pap_sample_${String(i + 7).padStart(2, '0')}`,
      { collection_id: 'col_samples', added_at: kst(4, `11:${String(i + 10).padStart(2, '0')}`) },
    ]),
  ]);

  const devices = [
    { device_id: 'dev_mac', name: 'MacBook Pro (개인)', connected_at: kst(20, '09:00'), last_active_at: kst(0, '14:22') },
    { device_id: 'dev_win', name: 'Windows 데스크톱 (학교)', connected_at: kst(15, '13:00'), last_active_at: kst(3, '18:03') },
  ];

  const me = { user_id: 'usr_demo', display_name: '예시 사용자', email: 'demo@example.com' };

  return { papers, annotations, collections, membership, devices, me, idempotency: new Map() };
}
