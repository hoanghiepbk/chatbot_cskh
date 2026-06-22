import type { CSSProperties } from "react";

// Render ANY user/agent/trace-derived text as PLAIN TEXT. React escapes string
// children by default, so `<script>` or markdown injection in `text` renders
// literally and never executes. This is the operator-facing anti-injection
// guarantee (TIP-007 acceptance decision): NEVER use dangerouslySetInnerHTML or
// a markdown renderer on customer/agent content anywhere in the console.
export function PlainText({
  text,
  className,
  style,
}: {
  text?: string | null;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <span className={className} style={style}>
      {text ?? ""}
    </span>
  );
}
