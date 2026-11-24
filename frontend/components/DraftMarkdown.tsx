"use client";

import type React from "react";
import { Tooltip } from "@/components/ui/Tooltip";
import type { FlaggedSpan, SourceRef } from "@/lib/api";

/**
 * Renders an appeal-letter draft written in a small Markdown subset
 * (headings, **bold**, *italic*, lists, > quotes, ---, paragraphs) while
 * keeping the two interactive overlays:
 *   - [S#] citation chips with a tooltip showing the cited policy text
 *   - groundedness-flagged sentences highlighted with the checker's reason
 *
 * A purpose-built renderer (rather than a Markdown library) because both
 * overlays must be applied to the raw text before formatting is parsed.
 */

type Props = {
  body: string;
  citations: Record<string, SourceRef>;
  flaggedSpans: FlaggedSpan[];
};

export default function DraftMarkdown({ body, citations, flaggedSpans }: Props) {
  const flags = flaggedSpans.filter((f) => f.span && f.span.trim().length > 0);
  const ctx: InlineCtx = { citations, flags, key: 0 };
  return <div className="space-y-3">{renderBlocks(body, ctx)}</div>;
}

// ── Blocks ────────────────────────────────────────────────────────────────────

type InlineCtx = {
  citations: Record<string, SourceRef>;
  flags: FlaggedSpan[];
  key: number;
};

function nextKey(ctx: InlineCtx, prefix: string) {
  ctx.key += 1;
  return `${prefix}-${ctx.key}`;
}

function renderBlocks(body: string, ctx: InlineCtx): React.ReactNode[] {
  const lines = body.replace(/\r\n/g, "\n").split("\n");
  const out: React.ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (trimmed === "") {
      i += 1;
      continue;
    }

    // Horizontal rule
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      out.push(<hr key={nextKey(ctx, "hr")} className="border-gray-200" />);
      i += 1;
      continue;
    }

    // Heading
    const h = trimmed.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const level = h[1].length;
      const cls =
        level <= 2
          ? "text-lg font-bold text-gray-900 font-sans"
          : "text-base font-semibold text-gray-900 font-sans";
      out.push(
        <div key={nextKey(ctx, "h")} role="heading" aria-level={level} className={cls}>
          {renderInline(h[2], ctx)}
        </div>
      );
      i += 1;
      continue;
    }

    // Blockquote (consecutive "> " lines)
    if (trimmed.startsWith(">")) {
      const quote: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        quote.push(lines[i].trim().replace(/^>\s?/, ""));
        i += 1;
      }
      const text = quote.join("\n").trim();
      if (text) {
        out.push(
          <blockquote
            key={nextKey(ctx, "q")}
            className="border-l-4 border-gray-200 pl-3 text-gray-600 italic space-y-2"
          >
            {renderBlocks(text, ctx)}
          </blockquote>
        );
      }
      continue;
    }

    // Lists (unordered or ordered)
    const isUl = (s: string) => /^[-*+]\s+/.test(s.trim());
    const isOl = (s: string) => /^\d+[.)]\s+/.test(s.trim());
    if (isUl(line) || isOl(line)) {
      const ordered = isOl(line);
      const items: string[] = [];
      while (i < lines.length && (ordered ? isOl(lines[i]) : isUl(lines[i]))) {
        items.push(lines[i].trim().replace(ordered ? /^\d+[.)]\s+/ : /^[-*+]\s+/, ""));
        i += 1;
        // continuation lines (indented, not a new item)
        while (
          i < lines.length &&
          lines[i].trim() !== "" &&
          /^\s{2,}/.test(lines[i]) &&
          !isUl(lines[i]) &&
          !isOl(lines[i])
        ) {
          items[items.length - 1] += " " + lines[i].trim();
          i += 1;
        }
      }
      const ListTag = ordered ? "ol" : "ul";
      out.push(
        <ListTag
          key={nextKey(ctx, "list")}
          className={`${ordered ? "list-decimal" : "list-disc"} pl-6 space-y-1`}
        >
          {items.map((it) => (
            <li key={nextKey(ctx, "li")}>{renderInline(it, ctx)}</li>
          ))}
        </ListTag>
      );
      continue;
    }

    // Paragraph: gather until a blank line or another block starts
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^(#{1,6})\s+/.test(lines[i].trim()) &&
      !lines[i].trim().startsWith(">") &&
      !isUl(lines[i]) &&
      !isOl(lines[i]) &&
      !/^(-{3,}|\*{3,}|_{3,})$/.test(lines[i].trim())
    ) {
      para.push(lines[i].trim());
      i += 1;
    }
    out.push(
      <p key={nextKey(ctx, "p")}>
        {para.map((p, idx) => (
          <span key={nextKey(ctx, "pl")}>
            {idx > 0 && <br />}
            {renderInline(p, ctx)}
          </span>
        ))}
      </p>
    );
  }
  return out;
}

// ── Inline: flags → citations → emphasis ─────────────────────────────────────

function renderInline(text: string, ctx: InlineCtx): React.ReactNode[] {
  // 1. Flagged spans (match on the raw text, including any ** markers)
  for (const flag of ctx.flags) {
    const idx = text.indexOf(flag.span);
    if (idx !== -1) {
      return [
        ...renderInline(text.slice(0, idx), ctx),
        <Tooltip
          key={nextKey(ctx, "flag")}
          content={
            <div>
              <div className="font-semibold text-amber-800">Groundedness flag</div>
              <div className="text-gray-700 mt-0.5">{flag.reason}</div>
            </div>
          }
        >
          <mark className="bg-amber-100 text-amber-900 rounded px-0.5 cursor-help hover:bg-amber-200">
            {renderCitationsAndEmphasis(flag.span, ctx)}
          </mark>
        </Tooltip>,
        ...renderInline(text.slice(idx + flag.span.length), ctx),
      ];
    }
  }
  return renderCitationsAndEmphasis(text, ctx);
}

function renderCitationsAndEmphasis(text: string, ctx: InlineCtx): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  for (const part of text.split(/(\[S\d+\])/g)) {
    if (!part) continue;
    const m = part.match(/^\[S(\d+)\]$/);
    if (m) {
      const key = `S${m[1]}`;
      const src = ctx.citations[key];
      out.push(
        <Tooltip
          key={nextKey(ctx, "cite")}
          content={
            src ? (
              <div className="space-y-1">
                <div className="font-semibold text-gray-800">{src.section}</div>
                <div className="text-gray-600 leading-snug">{src.text}</div>
                <div className="text-xs text-gray-400 mt-1">
                  doc {src.doc_id} · clause {src.clause_id}
                </div>
              </div>
            ) : (
              <span className="text-gray-500">Source not found for [{key}]</span>
            )
          }
        >
          <span className="inline cursor-help rounded bg-blue-100 px-1 text-blue-700 font-mono text-xs hover:bg-blue-200 not-italic">
            [{key}]
          </span>
        </Tooltip>
      );
    } else {
      out.push(...renderEmphasis(part, ctx));
    }
  }
  return out;
}

function renderEmphasis(text: string, ctx: InlineCtx): React.ReactNode[] {
  // **bold**, __bold__, *italic*, _italic_ (non-nested is enough for letters)
  const re = /(\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;
  const out: React.ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(re)) {
    const idx = m.index ?? 0;
    if (idx > last) out.push(text.slice(last, idx));
    const tok = m[0];
    if (tok.startsWith("**") || tok.startsWith("__")) {
      out.push(
        <strong key={nextKey(ctx, "b")} className="font-semibold">
          {renderEmphasis(tok.slice(2, -2), ctx)}
        </strong>
      );
    } else {
      out.push(<em key={nextKey(ctx, "i")}>{tok.slice(1, -1)}</em>);
    }
    last = idx + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
