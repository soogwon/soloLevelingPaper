export function remoteRecordText(record) {
  const highlight = record.kind === 'highlight';
  const label = highlight ? (record.body.trim() ? '메모가 연결된 하이라이트' : '하이라이트') : '메모';
  const quotes = record.segments.map(segment =>
    `${segment.target === 'translation' ? '번역' : '원문'}: ${segment.quote_snapshot}`);
  return [`${label} · revision ${record.revision}`, ...quotes,
    record.body && `${highlight ? '메모 · ' : ''}${record.body}`].filter(Boolean).join('\n');
}
