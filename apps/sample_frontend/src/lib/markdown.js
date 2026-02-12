import { marked } from "marked";

marked.setOptions({
  breaks: true,
  mangle: false,
  headerIds: false,
});

export function renderMarkdown(value) {
  return marked.parse(value || "");
}
