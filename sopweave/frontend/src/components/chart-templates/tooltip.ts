/** Escape workbook/model text at the HTML tooltip boundary. */
export function tooltipText(value: unknown): string {
  return String(value ?? '-').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]!));
}
