import Markdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"
import { cn } from "@/lib/utils"

interface DisclaimerBodyProps {
  text: string
  className?: string
}

const ALLOWED_ELEMENTS = ["p", "strong", "em", "ol", "ul", "li", "a"]

function isSafeResourceUrl(href: string | undefined): href is string {
  if (!href) return false

  try {
    const url = new URL(href)
    return url.protocol === "http:" || url.protocol === "https:"
  } catch {
    return false
  }
}

const COMPONENTS: Components = {
  p({ children }) {
    return <p>{children}</p>
  },
  strong({ children }) {
    return <strong className="font-semibold">{children}</strong>
  },
  em({ children }) {
    return <em className="italic">{children}</em>
  },
  ol({ children }) {
    return <ol className="list-decimal space-y-2 pl-5">{children}</ol>
  },
  ul({ children }) {
    return <ul className="list-disc space-y-1.5 pl-5">{children}</ul>
  },
  li({ children }) {
    return <li className="pl-1">{children}</li>
  },
  a({ href, children }) {
    if (!isSafeResourceUrl(href)) return <>{children}</>

    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="break-words font-medium underline underline-offset-2 hover:opacity-80 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-current"
      >
        {children}
      </a>
    )
  },
}

/** Render the safe Markdown subset used by backend-authored disclaimer copy. */
export default function DisclaimerBody({ text, className }: DisclaimerBodyProps) {
  return (
    <div className={cn("space-y-3 leading-relaxed", className)}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        allowedElements={ALLOWED_ELEMENTS}
        unwrapDisallowed
        components={COMPONENTS}
      >
        {text}
      </Markdown>
    </div>
  )
}
