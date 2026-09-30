(() => {
  "use strict";
  const { escapeHTML } = window.Meet2Notes;

  function renderInlineMarkdown(source, citationRenderer = null) {
    const emphasis = (value) => escapeHTML(value)
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_]+)__/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/(^|[^_])_([^_\n]+)_/g, "$1<em>$2</em>")
      .replace(/~~([^~]+)~~/g, "<del>$1</del>");
    const value = String(source || "");
    // Tokenize before escaping so code and URLs are never interpreted as emphasis.
    const pattern = /`([^`]+)`|(!?)\[([^\]]+)\]\(([^\s)]+)(?:\s+"[^"]*")?\)|\[([AR]\d+)\]/g;
    let result = "";
    let end = 0;
    for (const match of value.matchAll(pattern)) {
      result += emphasis(value.slice(end, match.index));
      const [, code, image, label, href, citation] = match;
      if (code !== undefined) result += `<code>${escapeHTML(code)}</code>`;
      else if (citation) result += citationRenderer ? citationRenderer(citation) : escapeHTML(match[0]);
      else if (image) result += emphasis(label); // Never fetch model-generated image URLs.
      else {
        const safeHref = /^(https?:\/\/|mailto:|#)/i.test(href) ? href : "#";
        const external = /^https?:\/\//i.test(safeHref)
          ? ' target="_blank" rel="noopener noreferrer"' : "";
        result += `<a href="${escapeHTML(safeHref)}"${external}>${emphasis(label)}</a>`;
      }
      end = match.index + match[0].length;
    }
    return result + emphasis(value.slice(end));
  }

  function isMarkdownBlockStart(lines, index) {
    const line = lines[index] || "";
    const next = lines[index + 1] || "";
    return /^\s*(```|~~~|#{1,6}\s|>|[-+*]\s+|\d+[.)]\s+|([-*_])(?:\s*\2){2,}\s*$)/.test(line)
      || (line.includes("|") && /^\s*\|?\s*:?-{3,}/.test(next));
  }

  function renderMarkdown(source, depth = 0, citationRenderer = null) {
    const inline = (text) => renderInlineMarkdown(text, citationRenderer);
    if (depth > 32) return `<p>${escapeHTML(String(source || ""))}</p>`;
    const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
    const output = [];
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) {
        index += 1;
        continue;
      }
      const fence = line.match(/^\s*(```|~~~)(.*)$/);
      if (fence) {
        const body = [];
        index += 1;
        while (index < lines.length && !new RegExp(`^\\s*${fence[1]}`).test(lines[index])) {
          body.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        output.push(`<pre><code>${escapeHTML(body.join("\n"))}</code></pre>`);
        continue;
      }
      const heading = line.match(/^\s*(#{1,6})\s+(.+?)\s*#*\s*$/);
      if (heading) {
        const level = heading[1].length;
        output.push(`<h${level}>${inline(heading[2])}</h${level}>`);
        index += 1;
        continue;
      }
      if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
        output.push("<hr>");
        index += 1;
        continue;
      }
      if (line.includes("|") && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] || "")) {
        const splitRow = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
        const headers = splitRow(line);
        index += 2;
        const rows = [];
        while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
          rows.push(splitRow(lines[index]));
          index += 1;
        }
        output.push(`<table><thead><tr>${headers.map((cell) => `<th>${inline(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${headers.map((_header, cellIndex) => `<td>${inline(row[cellIndex] || "")}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
        continue;
      }
      if (/^\s*>/.test(line)) {
        const quoted = [];
        while (index < lines.length && /^\s*>/.test(lines[index])) {
          quoted.push(lines[index].replace(/^\s*>\s?/, ""));
          index += 1;
        }
        output.push(`<blockquote>${renderMarkdown(quoted.join("\n"), depth + 1, citationRenderer)}</blockquote>`);
        continue;
      }
      const listPattern = /^( *)([-+*]|\d+[.)])\s+(.+)/;
      const list = line.match(listPattern);
      if (list) {
        const indent = list[1].length;
        const ordered = /^\d/.test(list[2]);
        const tag = ordered ? "ol" : "ul";
        const start = ordered ? ` start="${parseInt(list[2], 10)}"` : "";
        const items = [];
        while (index < lines.length) {
          const item = lines[index].match(listPattern);
          if (!item || item[1].length !== indent || /^\d/.test(item[2]) !== ordered) break;
          const body = [item[3]];
          const contentIndent = lines[index].indexOf(item[3], item[1].length + item[2].length);
          index += 1;
          while (index < lines.length) {
            const nextItem = lines[index].match(listPattern);
            if (nextItem && nextItem[1].length <= indent) break;
            if (!lines[index].trim()) {
              let next = index + 1;
              while (next < lines.length && !lines[next].trim()) next += 1;
              if (next >= lines.length || lines[next].search(/\S/) <= indent) break;
            } else if (lines[index].search(/\S/) <= indent && isMarkdownBlockStart(lines, index)) {
              break;
            }
            body.push(lines[index].replace(new RegExp(`^ {0,${contentIndent}}`), ""));
            index += 1;
          }
          items.push(`<li>${renderMarkdown(body.join("\n"), depth + 1, citationRenderer)}</li>`);
          // Blank lines between items belong to the same list.
          let next = index;
          while (next < lines.length && !lines[next].trim()) next += 1;
          const nextItem = (lines[next] || "").match(listPattern);
          if (nextItem && nextItem[1].length === indent && /^\d/.test(nextItem[2]) === ordered) index = next;
        }
        output.push(`<${tag}${start}>${items.join("")}</${tag}>`);
        continue;
      }
      const paragraph = [line.trim()];
      index += 1;
      while (index < lines.length && lines[index].trim() && !isMarkdownBlockStart(lines, index)) {
        paragraph.push(lines[index].trim());
        index += 1;
      }
      output.push(`<p>${paragraph.map(inline).join("<br>")}</p>`);
    }
    return output.join("");
  }

  window.Meet2Notes.renderMarkdown = renderMarkdown;
  window.Meet2Notes.renderCitedAnswer = (text, citations = []) => {
    const { t } = window.Meet2Notes;
    const catalog = new Map(citations.map((source) => [source.citation_id, source]));
    return renderMarkdown(text, 0, (id) => {
      const source = catalog.get(id);
      if (!source || !Number.isSafeInteger(source.meeting_id) || source.meeting_id < 1) {
        return `<span class="assistant-citation unverified">${escapeHTML(t("assistant.source_unverified"))}</span>`;
      }
      const kind = source.kind === "summary" ? "notes" : source.kind === "transcription" ? "transcript" : "excerpt";
      const seconds = Math.floor(Math.max(0, Number(source.start_ms) || 0) / 1000);
      const time = source.kind === "excerpt" ? ` · ${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}` : "";
      const label = `${t(`assistant.source_${kind}`)}: ${source.meeting_title || source.label}${time}`;
      return `<a class="assistant-citation" href="/?meeting=${source.meeting_id}">${escapeHTML(label)}</a>`;
    });
  };
})();
