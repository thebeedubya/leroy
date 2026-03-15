import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const PLATFORMS = ['blog', 'linkedin', 'x', 'instagram']

const PLATFORM_LABELS = {
  blog: 'Blog',
  linkedin: 'LinkedIn',
  x: 'X Thread',
  instagram: 'Instagram',
}

function charCount(text) {
  return text ? text.length : 0
}

function BlogContent({ draft }) {
  const [showFrontMatter, setShowFrontMatter] = useState(false)
  if (!draft) return <EmptyPlatform />
  return (
    <div>
      {draft.front_matter && (
        <div className="mb-3">
          <button
            onClick={() => setShowFrontMatter((v) => !v)}
            className="text-xs text-slate-500 hover:text-slate-300 underline"
          >
            {showFrontMatter ? 'Hide front matter' : 'Show front matter'}
          </button>
          {showFrontMatter && (
            <pre className="mt-2 p-3 bg-forge-surface rounded text-xs text-slate-400 overflow-x-auto border border-forge-border">
              {draft.front_matter}
            </pre>
          )}
        </div>
      )}
      <div className="prose-content text-sm leading-relaxed">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{draft.content || ''}</ReactMarkdown>
      </div>
    </div>
  )
}

function LinkedInContent({ draft }) {
  if (!draft) return <EmptyPlatform />
  const count = charCount(draft.content)
  return (
    <div>
      <div className="flex justify-end mb-2">
        <span className={`text-xs ${count > 3000 ? 'text-red-400' : 'text-slate-500'}`}>
          {count} chars
        </span>
      </div>
      <pre className="whitespace-pre-wrap text-sm text-slate-200 leading-relaxed font-mono">{draft.content}</pre>
      {draft.posted_url && (
        <p className="mt-3 text-xs text-blue-400">
          Posted: <a href={draft.posted_url} target="_blank" rel="noreferrer" className="underline">{draft.posted_url}</a>
        </p>
      )}
    </div>
  )
}

function XContent({ draft }) {
  if (!draft) return <EmptyPlatform />
  // Split into individual tweets by numbered lines
  const tweets = draft.content
    .split('\n')
    .filter((l) => l.trim())
    .reduce((acc, line) => {
      if (/^\d+\//.test(line.trim())) {
        acc.push(line.trim())
      } else if (acc.length > 0) {
        acc[acc.length - 1] += '\n' + line
      } else {
        acc.push(line)
      }
      return acc
    }, [])

  return (
    <div className="space-y-2">
      {tweets.map((tweet, i) => {
        const count = charCount(tweet)
        return (
          <div key={i} className="p-3 bg-forge-surface rounded border border-forge-border">
            <pre className="whitespace-pre-wrap text-sm text-slate-200 font-mono leading-relaxed">{tweet}</pre>
            <div className="flex justify-end mt-1">
              <span className={`text-xs ${count > 280 ? 'text-red-400' : 'text-slate-500'}`}>
                {count}/280
              </span>
            </div>
          </div>
        )
      })}
      {tweets.length === 0 && (
        <pre className="whitespace-pre-wrap text-sm text-slate-200 font-mono">{draft.content}</pre>
      )}
    </div>
  )
}

function InstagramContent({ draft }) {
  if (!draft) return <EmptyPlatform />
  const slides = draft.carousel_slides || []
  return (
    <div>
      {draft.content && (
        <div className="mb-4">
          <p className="text-xs text-slate-500 uppercase tracking-wide mb-2">Caption</p>
          <pre className="whitespace-pre-wrap text-sm text-slate-200 font-mono leading-relaxed">{draft.content}</pre>
        </div>
      )}
      {slides.length > 0 && (
        <div>
          <p className="text-xs text-slate-500 uppercase tracking-wide mb-2">Carousel Slides ({slides.length})</p>
          <div className="space-y-1.5">
            {slides.map((slide, i) => (
              <div key={i} className="p-2.5 bg-forge-surface rounded border border-forge-border text-sm text-slate-300">
                {slide}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function EmptyPlatform() {
  return <p className="text-sm text-slate-500 italic">No draft available for this platform.</p>
}

export default function PlatformTabs({ platforms }) {
  const available = PLATFORMS.filter((p) => platforms && platforms[p])
  const [activeTab, setActiveTab] = useState(available[0] || 'blog')

  if (!platforms || Object.keys(platforms).length === 0) {
    return <p className="text-sm text-slate-500 italic">No platform drafts found.</p>
  }

  const draft = platforms[activeTab]

  return (
    <div>
      {/* Tab bar */}
      <div className="flex gap-1 mb-4 border-b border-forge-border pb-2">
        {PLATFORMS.map((p) => {
          const hasDraft = !!platforms[p]
          return (
            <button
              key={p}
              onClick={() => setActiveTab(p)}
              disabled={!hasDraft}
              className={`px-3 py-1.5 rounded-t text-xs font-medium transition-colors ${
                activeTab === p
                  ? 'bg-blue-600 text-white'
                  : hasDraft
                  ? 'text-slate-400 hover:text-slate-200 hover:bg-forge-muted'
                  : 'text-slate-600 cursor-not-allowed'
              }`}
            >
              {PLATFORM_LABELS[p]}
            </button>
          )
        })}
      </div>

      {/* Content */}
      <div className="min-h-24">
        {activeTab === 'blog' && <BlogContent draft={draft} />}
        {activeTab === 'linkedin' && <LinkedInContent draft={draft} />}
        {activeTab === 'x' && <XContent draft={draft} />}
        {activeTab === 'instagram' && <InstagramContent draft={draft} />}
      </div>
    </div>
  )
}
