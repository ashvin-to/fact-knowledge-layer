import {
  DocumentIcon,
  UploadIcon,
  CompareIcon,
  GraphIcon,
  CommandIcon,
  GitCommitIcon,
} from "./Icons";

const NAV = [
  { id: "documents", label: "Documents", Icon: DocumentIcon },
  { id: "upload", label: "Upload PDF", Icon: UploadIcon },
  { id: "compare", label: "Comparison", Icon: CompareIcon },
  { id: "synthesis", label: "Multi-Hop Synthesis", Icon: GitCommitIcon },
  { id: "graph", label: "Knowledge Graph", Icon: GraphIcon },
];


function Monogram() {
  return (
    <svg width="30" height="30" viewBox="0 0 30 30" fill="none" aria-hidden="true">
      <rect width="30" height="30" rx="8" fill="url(#fx-grad)" />
      <path
        d="M10 21V9h9M10 15h6.5"
        stroke="#fff"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <defs>
        <linearGradient id="fx-grad" x1="0" y1="0" x2="30" y2="30">
          <stop stopColor="#6a5cff" />
          <stop offset="1" stopColor="#38bdf8" />
        </linearGradient>
      </defs>
    </svg>
  );
}

export default function Sidebar({
  currentTab,
  onSelectTab,
  totalDocs,
  totalFacts,
}) {
  return (
    <aside className="sidebar">
      <div
        className="side-brand"
        onClick={() => onSelectTab("documents")}
        title="Fact Extraction & Comparison"
      >
        <span className="side-logo">
          <Monogram />
        </span>
        <span className="side-brand-text">
          <span className="side-brand-name">FactLens</span>
          <span className="side-brand-sub">Facts · Compare · Graph</span>
        </span>
      </div>

      <nav className="side-scroll">
        <span className="side-section-label">Workspace</span>
        {NAV.map(({ id, label, Icon }) => (
          <button
            key={id}
            className={`side-item ${currentTab === id ? "active" : ""}`}
            onClick={() => onSelectTab(id)}
            title={label}
          >
            <span className="side-item-icon">
              <Icon size={17} />
            </span>
            <span className="side-item-label">{label}</span>
            {id === "documents" && totalDocs > 0 && (
              <span className="side-count">{totalDocs}</span>
            )}
          </button>
        ))}
      </nav>

      <div className="side-foot">
        <div className="side-stat-line">
          <b>{totalDocs}</b> docs
          <span className="side-stat-sep">·</span>
          <b>{totalFacts.toLocaleString()}</b> facts
        </div>
      </div>
    </aside>
  );
}

