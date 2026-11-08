import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { ReportSummary, type SavedSummary } from "./ReportSummary";
import {
  PAGES,
  WorkspacePages,
  type Page,
  type Workspace,
} from "./WorkspacePages";

type Analysis = "demand" | "supply" | "failure" | "inventory";
type Config = {
  name: string;
  factory: string;
  horizon_days: number;
  model: string;
  demand_change_pct: number;
  extra_delay_days: number;
  analyses: Analysis[];
};
type Options = {
  models: string[];
  site: string;
  planning_date: string;
  factories: { id: string; name: string }[];
};
type Saved = {
  id: string;
  name: string;
  site: string;
  created_at: string;
  config: Config;
  demo_locked: boolean;
};
type Candidate = {
  supplier: string | null;
  shipping: string;
  quantity: number;
  arrival_date: string | null;
  purchase_cost: number;
  modeled_cost: number;
  shortage_units: number;
  ending_stock: number;
};
type Recommendation = Candidate & {
  id: string;
  component: string;
  status: string;
  order_id?: string;
  reason: string;
  avoided_shortage_units: number;
  candidates: Candidate[];
};
type Report = {
  id: string;
  name: string;
  created_at: string;
  planning_date: string;
  end_date: string;
  history_provenance?: string;
  site: string;
  config: Config;
  is_stale: boolean;
  demo_locked: boolean;
  summary: {
    forecast_units: number;
    inventory_value: number;
    components_at_risk: number;
    recommended_purchase_cost: number;
  };
  forecast: {
    method: string;
    range_label: string;
    evaluation: string;
    history: { week: string; units: number }[];
    future: { week: string; units: number; lower: number; upper: number }[];
    models: {
      model: string;
      predicted_units: number;
      daily_units: number;
      holdout_mae: number;
      holdout_wape_pct: number;
    }[];
  };
  suppliers: {
    supplier: string;
    observations: number;
    expected_delay_days: number;
    historical_p90_delay_days: number;
    failure_rate_pct: number;
    last_observed: string;
    stale: boolean;
  }[];
  inventory: {
    id: string;
    name: string;
    on_hand: number;
    reserved: number;
    available: number;
    incoming_units: number;
    required_units: number;
    coverage_days: number;
    projected_shortage: number;
    unit_cost: number;
    eligible_suppliers: string[];
    failure_rate_proxy_pct: number;
  }[];
  recommendations: Recommendation[];
  risks: {
    id: string;
    kind: string;
    severity: string;
    title: string;
    detail: string;
  }[];
  customer_orders: {
    id: string;
    customer: string;
    model: string;
    quantity: number;
    due_date: string;
    status: string;
  }[];
  planned_orders: {
    id: string;
    component_id: string;
    supplier: string;
    quantity: number;
    expected_date: string;
    status: string;
  }[];
  assumptions: string[];
  source_rows: number;
  ai_summary: SavedSummary | null;
};
const MODULES: { id: Analysis; title: string; detail: string }[] = [
  { id: "demand", title: "Demand forecast", detail: "Expected tractor demand" },
  { id: "supply", title: "Supply risk", detail: "Supplier delay exposure" },
  {
    id: "failure",
    title: "Component failure",
    detail: "Expected defect-rate proxy",
  },
  {
    id: "inventory",
    title: "Inventory recommendations",
    detail: "What to buy and why",
  },
];
const DEFAULT: Config = {
  factory: "CA",
  name: "Supply planning report",
  horizon_days: 60,
  model: "",
  demand_change_pct: 0,
  extra_delay_days: 0,
  analyses: MODULES.map((module) => module.id),
};
const number = (value: number) => new Intl.NumberFormat("en-US").format(value);
const money = (value: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
const day = (value: string) =>
  new Date(`${value}T00:00:00`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
const savedTime = (value: string) =>
  new Date(value).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });

async function api<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
  method?: "DELETE",
): Promise<T> {
  const response = await fetch(
    `/api${path}`,
    body === undefined
      ? { signal, method }
      : {
          signal,
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : `Request failed (${response.status}). Check the report settings and try again.`,
    );
  }
  return response.json();
}

function Pill({
  children,
  tone = "",
}: {
  children: React.ReactNode;
  tone?: string;
}) {
  return <span className={`pill ${tone}`}>{children}</span>;
}
function DemandChart({ forecast }: { forecast: Report["forecast"] }) {
  const points = [...forecast.history, ...forecast.future];
  if (!points.length) return null;
  const left = 58,
    width = 860,
    top = 18,
    height = 205;
  const first = Date.parse(points[0].week),
    last = Date.parse(points[points.length - 1].week);
  const maximum =
    Math.max(
      ...forecast.history.map((point) => point.units),
      ...forecast.future.map((point) => point.upper),
      1,
    ) * 1.08;
  const x = (week: string) =>
    left + ((Date.parse(week) - first) / Math.max(last - first, 1)) * width;
  const y = (units: number) => top + height * (1 - units / maximum);
  const historical = forecast.history
    .map((point) => `${x(point.week)},${y(point.units)}`)
    .join(" ");
  const predicted = [...forecast.history.slice(-1), ...forecast.future]
    .map((point) => `${x(point.week)},${y(point.units)}`)
    .join(" ");
  const band = [
    ...forecast.future.map((point) => `${x(point.week)},${y(point.upper)}`),
    ...[...forecast.future]
      .reverse()
      .map((point) => `${x(point.week)},${y(point.lower)}`),
  ].join(" ");
  return (
    <>
      <svg
        className="chart"
        viewBox="0 0 960 268"
        role="img"
        aria-label="Historical weekly demand and forecast with empirical historical range"
      >
        {[0, 0.5, 1].map((fraction) => (
          <g key={fraction}>
            <line
              x1={left}
              x2={left + width}
              y1={top + height * fraction}
              y2={top + height * fraction}
              stroke="#e6ebe9"
            />
            <text x={left - 9} y={top + height * fraction + 4} textAnchor="end">
              {number(Math.round(maximum * (1 - fraction)))}
            </text>
          </g>
        ))}
        <polygon points={band} fill="#dcece6" />
        <polyline
          points={historical}
          fill="none"
          stroke="#32776a"
          strokeWidth="2.5"
        />
        <polyline
          points={predicted}
          fill="none"
          stroke="#c2773b"
          strokeWidth="2.5"
          strokeDasharray="7 5"
        />
        {forecast.future.map((point) => (
          <circle
            key={point.week}
            cx={x(point.week)}
            cy={y(point.units)}
            r="3"
            fill="#c2773b"
          >
            <title>{`${point.week}: ${number(point.units)} forecast units`}</title>
          </circle>
        ))}
        <text x={left} y={255}>
          {day(points[0].week)}
        </text>
        <text x={x(forecast.future[0].week)} y={255} textAnchor="middle">
          Forecast starts
        </text>
        <text x={left + width} y={255} textAnchor="end">
          {day(points[points.length - 1].week)}
        </text>
      </svg>
      <p className="footnote">
        Weekly tractor units · {forecast.range_label}. Last forecast week may be
        partial. Factory demand scenario includes confirmed orders when they
        exceed the daily forecast.
      </p>
    </>
  );
}

const FACTORY_IDS = ["CA", "FL", "IL", "NY", "TX"];
const workspacePath = (site: string) =>
  site === "ALL" ? "/workspace" : `/factories/${site}/workspace`;
const reportsPath = (site: string) =>
  site === "ALL" ? "/reports" : `/reports?factory=${site}`;
function initialFactory() {
  const value =
    new URLSearchParams(window.location.search).get("factory") ||
    localStorage.getItem("tractor.factory") ||
    "ALL";
  return value === "ALL" || FACTORY_IDS.includes(value) ? value : "ALL";
}
function initialPage(): Page {
  const query = new URLSearchParams(window.location.search);
  if (query.has("report")) return "reports";
  const value = query.get("page");
  return PAGES.some((page) => page.id === value) ? (value as Page) : "home";
}
function App() {
  const [page, setPage] = useState<Page>(initialPage);
  const [factory, setFactory] = useState(initialFactory);
  const [view, setView] = useState<"library" | "editor">("library");
  const [search, setSearch] = useState("");
  const [options, setOptions] = useState<Options | null>(null);
  const [draft, setDraft] = useState<Config>({
    ...DEFAULT,
    factory: factory === "ALL" ? "" : factory,
  });
  const [report, setReport] = useState<Report | null>(null);
  const [saved, setSaved] = useState<Saved[]>([]);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [workspaceLoading, setWorkspaceLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [decisionBusy, setDecisionBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [booting, setBooting] = useState(true);
  const navigationVersion = useRef(0);
  const locationKey = useRef(window.location.pathname + window.location.search);
  const show = (module: Analysis) => report?.config.analyses.includes(module);
  const invalid =
    !FACTORY_IDS.includes(draft.factory) ||
    !draft.name.trim() ||
    !draft.analyses.length ||
    draft.horizon_days < 7 ||
    draft.horizon_days > 90;

  function route(next: Page, site = factory, id?: string, editor = false) {
    navigationVersion.current += 1;
    const url = new URL(window.location.href);
    url.search = new URLSearchParams({ page: next, factory: site }).toString();
    if (id) url.searchParams.set("report", id);
    else if (editor) url.searchParams.set("view", "new");
    url.hash = "";
    if (url.href !== window.location.href) {
      window.history.pushState(null, "", url);
    }
    locationKey.current = url.pathname + url.search;
    window.scrollTo(0, 0);
  }
  function go(next: Page) {
    setPage(next);
    setError("");
    setNotice("");
    if (next === "reports") setView("library");
    route(next);
  }
  function selectFactory(site: string) {
    setFactory(site);
    localStorage.setItem("tractor.factory", site);
    setSearch("");
    setError("");
    setNotice("");
    route(
      page,
      site,
      page === "reports" && view === "editor" && report ? report.id : undefined,
      page === "reports" && view === "editor",
    );
  }
  function applyReport(value: Report, navigate = true) {
    setReport(value);
    setDraft({ ...DEFAULT, ...value.config });
    setView("editor");
    setPage("reports");
    if (navigate) route("reports", factory, value.id);
  }
  function newReport() {
    setReport(null);
    setDraft({
      ...DEFAULT,
      factory: factory === "ALL" ? "" : factory,
      analyses: [...DEFAULT.analyses],
    });
    setPage("reports");
    setView("editor");
    setError("");
    setNotice("");
    route("reports", factory, undefined, true);
  }
  function library() {
    go("reports");
  }
  async function refreshWorkspace(site = factory) {
    const [data, runs] = await Promise.all([
      api<Workspace>(workspacePath(site)),
      api<Saved[]>(reportsPath(site)),
    ]);
    if (site !== initialFactory()) return;
    setWorkspace(data);
    setSaved(runs);
  }
  async function initialize() {
    setError("");
    setBooting(true);
    try {
      setOptions(await api<Options>("/report-options"));
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBooting(false);
    }
  }
  useEffect(() => {
    // Pin the initial scope so Back restores it even after localStorage changes.
    const url = new URL(window.location.href);
    url.searchParams.set("page", initialPage());
    url.searchParams.set("factory", initialFactory());
    window.history.replaceState(null, "", url);
    locationKey.current = url.pathname + url.search;
    function restoreLocation() {
      const site = initialFactory();
      const next = initialPage();
      const query = new URLSearchParams(window.location.search);
      navigationVersion.current += 1;
      setFactory(site);
      localStorage.setItem("tractor.factory", site);
      setPage(next);
      setSearch("");
      setError("");
      setNotice("");
      setReport(null);
      setBusy(false);
      setDraft({ ...DEFAULT, factory: site === "ALL" ? "" : site,
        analyses: [...DEFAULT.analyses] });
      setView(next === "reports" && query.get("view") === "new" ? "editor" : "library");
      const id = query.get("report");
      if (id) void load(id, false);
    }
    function onPopState() {
      const key = window.location.pathname + window.location.search;
      // Section anchors share the same view and should retain draft settings.
      if (key === locationKey.current) return;
      locationKey.current = key;
      restoreLocation();
    }
    window.addEventListener("popstate", onPopState);
    restoreLocation();
    void initialize();
    return () => {
      navigationVersion.current += 1;
      window.removeEventListener("popstate", onPopState);
    };
  }, []);
  useEffect(() => {
    if (!options) return;
    const controller = new AbortController();
    setWorkspaceLoading(true);
    setWorkspace(null);
    setSaved([]);
    Promise.all([
      api<Workspace>(workspacePath(factory), undefined, controller.signal),
      api<Saved[]>(reportsPath(factory), undefined, controller.signal),
    ])
      .then(([data, runs]) => {
        if (!controller.signal.aborted) {
          setWorkspace(data);
          setSaved(runs);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(error.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setWorkspaceLoading(false);
      });
    return () => controller.abort();
  }, [factory, options]);
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [page, view, factory, report?.id]);

  async function run(config = draft, recalculateId = report?.id) {
    let version = navigationVersion.current;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const value = await api<Report>(
        recalculateId ? `/reports/${recalculateId}/recalculate` : "/reports",
        recalculateId ? { config } : config,
      );
      if (version !== navigationVersion.current) return;
      applyReport(value);
      version = navigationVersion.current;
      await refreshWorkspace();
    } catch (error) {
      if (version === navigationVersion.current) setError((error as Error).message);
    } finally {
      if (version === navigationVersion.current) setBusy(false);
    }
  }
  async function load(id: string, navigate = true) {
    if (!id) return;
    const version = ++navigationVersion.current;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const value = await api<Report>(`/reports/${id}`);
      if (version === navigationVersion.current) {
        setBusy(false);
        applyReport(value, navigate);
      }
    } catch (error) {
      if (version === navigationVersion.current) setError((error as Error).message);
    } finally {
      if (version === navigationVersion.current) setBusy(false);
    }
  }
  async function decide(id: string, action: "approved" | "denied") {
    if (!report) return;
    setDecisionBusy(id);
    setError("");
    setNotice("");
    try {
      setReport(
        await api<Report>(
          `/reports/${report.id}/recommendations/${id}/decision`,
          { action },
        ),
      );
      await refreshWorkspace();
      setNotice(
        action === "approved"
          ? "Approved. An internal planned order was created. Run a new report to include it in projected inventory."
          : "Recommendation denied. The decision is saved with this report.",
      );
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setDecisionBusy("");
    }
  }
  async function deleteReport(value: Saved | Report) {
    if (value.demo_locked || !window.confirm(
      `Delete “${value.name}”? Approved purchase orders will be kept.`,
    )) return;
    let version = navigationVersion.current;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/reports/${value.id}`, undefined, undefined, "DELETE");
      if (version !== navigationVersion.current) return;
      if (report?.id === value.id) {
        setReport(null);
        go("reports");
        version = navigationVersion.current;
      }
      await refreshWorkspace();
      if (version === navigationVersion.current) {
        setNotice("Report deleted. Approved purchase orders have been kept.");
      }
    } catch (error) {
      if (version === navigationVersion.current) setError((error as Error).message);
    } finally {
      if (version === navigationVersion.current) setBusy(false);
    }
  }
  function exportReport() {
    if (!report) return;
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `supply-report-${report.id.slice(0, 8)}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }
  const dirty =
    report &&
    (Object.keys(DEFAULT) as (keyof Config)[]).some(
      (key) =>
        JSON.stringify(draft[key]) !== JSON.stringify(report.config[key]),
    );
  const updating = busy || !!decisionBusy || booting;
  const filteredSaved = saved.filter(
    (value) =>
      (factory === "ALL" || value.site === factory) &&
      value.name.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <>
      <header className="topbar">
        <button
          className="brand"
          aria-label="Tractor home"
          onClick={() => go("home")}
          disabled={updating}
        >
          <span className="brand-icon" aria-hidden="true">
            T
          </span>{" "}
          Tractor <span className="brand-light">/ Supply planning</span>
        </button>
        <div className="topbar-right">
          <label className="global-factory">
            Factory
            <select
              aria-label="Factory"
              value={factory}
              disabled={updating || !options}
              onChange={(event) => selectFactory(event.target.value)}
            >
              <option value="ALL">All factories</option>
              {options ? (
                options.factories.map((value) => (
                  <option key={value.id} value={value.id}>
                    {value.name} ({value.id})
                  </option>
                ))
              ) : factory !== "ALL" ? (
                <option value={factory}>{factory}</option>
              ) : null}
            </select>
          </label>
          <Pill>Demo workspace</Pill>
        </div>
      </header>
      <div className="workspace-shell">
        <aside className="sidebar" aria-label="Workspace navigation">
          <p className="sidebar-caption">WORKSPACE</p>
          <nav className="sidebar-nav" aria-label="Main navigation">
            {PAGES.map((item) => (
              <button
                key={item.id}
                className={page === item.id ? "nav-item active" : "nav-item"}
                disabled={updating}
                aria-current={page === item.id ? "page" : undefined}
                onClick={() => go(item.id)}
              >
                <span aria-hidden="true">{item.icon}</span>
                {item.name}
              </button>
            ))}
          </nav>
          {page === "reports" && view === "editor" && report && (
            <div className="sidebar-report">
              <p className="sidebar-caption">CURRENT REPORT</p>
              <strong>{report.name}</strong>
              <span className="sidebar-context">
                Factory {report.site} · {report.config.horizon_days} days
              </span>
              <nav
                className="sidebar-sections"
                aria-label="Current report navigation"
              >
                {show("demand") && <a href="#forecast">Demand forecast</a>}
                {(show("supply") || show("failure")) && (
                  <a href="#risk">Supplier risk</a>
                )}
                {show("failure") && (
                  <a href="#component-risk">Component failure</a>
                )}
                <a href="#inventory">Current inventory</a>
                {show("inventory") && (
                  <a href="#recommendations">Recommendations</a>
                )}
                <a href="#orders">Customer orders</a>
              </nav>
            </div>
          )}
          <div className="sidebar-footer">
            <span className="site-dot" />
            <div>
              Local demo workspace
              <small>Synthetic demo · mock operations</small>
            </div>
          </div>
        </aside>
        <main>
          <div className="heading">
            <div>
              <p className="eyebrow">REPORT WORKSPACE</p>
              <h1>
                {page === "reports"
                  ? view === "library"
                    ? "Your planning reports."
                    : "Planning report"
                  : page === "home"
                    ? "Factory overview"
                    : PAGES.find((item) => item.id === page)?.name}
              </h1>
              <p className="subtitle">
                {page === "reports" && view === "editor"
                  ? "Choose what to predict. Review the risks. Decide what to buy."
                  : PAGES.find((item) => item.id === page)?.subtitle}
              </p>
            </div>
            <div className="heading-actions">
              {page === "reports" && view === "editor" && (
                <button
                  className="secondary"
                  disabled={updating}
                  onClick={library}
                >
                  All reports
                </button>
              )}
              {page === "reports" && view === "editor" && report && (
                <>
                  {report.demo_locked ? <Pill>Demo · locked</Pill> : (
                    <button className="secondary" disabled={updating}
                      onClick={() => void deleteReport(report)}>Delete report</button>
                  )}
                  <button
                    className="secondary"
                    disabled={updating}
                    onClick={exportReport}
                  >
                    Export JSON ↗
                  </button>
                  <button
                    className="secondary"
                    disabled={updating}
                    onClick={() => window.print()}
                  >
                    Print report
                  </button>
                </>
              )}
              {page === "reports" && view === "library" && (
                <button
                  className="primary"
                  disabled={updating}
                  onClick={newReport}
                >
                  + New report
                </button>
              )}
            </div>
          </div>
          {page === "reports" && view === "editor" && (
            <section
              className="configure panel"
              aria-label="Report configuration"
            >
              <div className="panel-heading">
                <div>
                  <h2>Configure your report</h2>
                  <p>
                    Run a new snapshot with the questions and assumptions that
                    matter.
                  </p>
                </div>
                <span className="subtle">On demand · saved automatically</span>
              </div>
              <label className="name-field">
                Report name
                <input
                  aria-label="Report name"
                  value={draft.name}
                  maxLength={100}
                  disabled={updating}
                  placeholder="e.g. January supply review"
                  onChange={(event) =>
                    setDraft({ ...draft, name: event.target.value })
                  }
                />
              </label>
              <fieldset className="modules" disabled={updating}>
                <legend className="sr-only">
                  What do you want to predict?
                </legend>
                {MODULES.map((module) => (
                  <label
                    key={module.id}
                    className={`module ${draft.analyses.includes(module.id) ? "selected" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={draft.analyses.includes(module.id)}
                      onChange={() =>
                        setDraft({
                          ...draft,
                          analyses: draft.analyses.includes(module.id)
                            ? draft.analyses.filter(
                                (value) => value !== module.id,
                              )
                            : [...draft.analyses, module.id],
                        })
                      }
                    />
                    <span>
                      <strong>{module.title}</strong>
                      <small>{module.detail}</small>
                    </span>
                  </label>
                ))}
              </fieldset>
              <div className="settings">
                <label>
                  Report factory / state
                  <select
                    aria-label="Report factory / state"
                    value={draft.factory}
                    disabled={updating || !options || !!report}
                    onChange={(event) =>
                      setDraft({ ...draft, factory: event.target.value })
                    }
                  >
                    <option value="" disabled>
                      Choose a factory…
                    </option>
                    {options?.factories.map((site) => (
                      <option key={site.id} value={site.id}>
                        {site.name} ({site.id})
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Forecast horizon
                  <div className="input-unit">
                    <input
                      aria-label="Forecast horizon"
                      type="number"
                      min="7"
                      max="90"
                      value={draft.horizon_days}
                      disabled={updating}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          horizon_days: Number(event.target.value),
                        })
                      }
                    />
                    <span>days</span>
                  </div>
                </label>
                <label>
                  Forecast model
                  <select
                    aria-label="Forecast model"
                    value={draft.model}
                    disabled={updating || !options}
                    onChange={(event) =>
                      setDraft({ ...draft, model: event.target.value })
                    }
                  >
                    <option value="">All tractor models</option>
                    {options?.models.map((model) => (
                      <option key={model}>{model}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Demand change
                  <div className="input-unit">
                    <input
                      aria-label="Demand change"
                      type="number"
                      min="-50"
                      max="100"
                      value={draft.demand_change_pct}
                      disabled={updating}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          demand_change_pct: Number(event.target.value),
                        })
                      }
                    />
                    <span>%</span>
                  </div>
                </label>
                <label>
                  Extra supplier delay
                  <div className="input-unit">
                    <input
                      aria-label="Extra supplier delay"
                      type="number"
                      min="0"
                      max="30"
                      value={draft.extra_delay_days}
                      disabled={updating}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          extra_delay_days: Number(event.target.value),
                        })
                      }
                    />
                    <span>days</span>
                  </div>
                </label>
                <button
                  className="primary run-button"
                  disabled={invalid || updating || !options}
                  onClick={() => void run()}
                >
                  {busy ? "Running report…" : report ? "Run next version →" : "Run report →"}
                </button>
              </div>
              <div className="config-notes">
                <span>
                  Report calculations use the factory selected above,
                  independently of the global browsing filter. Inventory
                  recommendations calculate demand and risk inputs
                  automatically. Shared parts are always planned for the whole
                  factory. {report && "Use New report from the report library to select another factory."}
                </span>
                <span>
                  {!draft.analyses.length
                    ? "Select at least one analysis."
                    : dirty
                      ? "Settings changed. Run a report to apply them."
                      : "Choose 7–90 days."}
                </span>
              </div>
            </section>
          )}
          {error && (
            <div className="error" role="alert">
              {error}
              {!options && (
                <button onClick={() => void initialize()}>
                  Retry connection
                </button>
              )}
            </div>
          )}
          {notice && (
            <div className="success" role="status">
              {notice}
            </div>
          )}
          {booting || workspaceLoading ? (
            <div className="empty panel" role="status">
              Loading your workspace…
            </div>
          ) : page !== "reports" ? (
            workspace && workspace.site === factory ? (
              <WorkspacePages
                key={`${page}-${factory}`}
                page={page}
                data={workspace}
                onNavigate={go}
                onOpenReport={(id) => void load(id)}
              />
            ) : (
              <div className="empty panel">
                <p>Factory data could not be loaded.</p>
                <button className="secondary" onClick={() => void initialize()}>
                  Retry
                </button>
              </div>
            )
          ) : view === "library" ? (
            <section className="panel library" aria-label="Saved reports">
              <div className="panel-heading">
                <div>
                  <h2>
                    Saved reports{" "}
                    <span className="subtle">({saved.length})</span>
                  </h2>
                  <p>
                    Open a saved snapshot, or start a new report with different
                    questions.
                  </p>
                </div>
                <div className="library-filters">
                  <input
                    className="search"
                    aria-label="Search reports"
                    placeholder="Search reports…"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                </div>
              </div>
              {saved.length ? (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Report name</th>
                        <th scope="col">Factory</th>
                        <th scope="col">Created</th>
                        <th scope="col">Horizon</th>
                        <th scope="col">Forecast scope</th>
                        <th scope="col">Analyses</th>
                        <th scope="col">
                          <span className="sr-only">Report actions</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredSaved.map((value) => (
                        <tr key={value.id}>
                          <td>
                            <button
                              className="report-link"
                              onClick={() => void load(value.id)}
                              disabled={updating}
                            >
                              {value.name}
                            </button>
                            <span className="library-id">
                              #{value.id.slice(0, 8)}
                            </span>
                            {value.demo_locked && <Pill>Demo · locked</Pill>}
                          </td>
                          <td>
                            <Pill>{value.site}</Pill>
                          </td>
                          <td>{savedTime(value.created_at)}</td>
                          <td>{value.config.horizon_days} days</td>
                          <td>{value.config.model || "All models"}</td>
                          <td>
                            <div className="analysis-tags">
                              {value.config.analyses.map((analysis) => (
                                <Pill key={analysis}>
                                  {
                                    MODULES.find(
                                      (module) => module.id === analysis,
                                    )?.title
                                  }
                                </Pill>
                              ))}
                            </div>
                          </td>
                          <td>
                            <div className="report-actions">
                              <button
                                className="secondary"
                                disabled={updating}
                                onClick={() => void load(value.id)}
                              >
                                Open →
                              </button>
                              <button className="secondary"
                                aria-label={`Delete ${value.name} (${value.id.slice(0, 8)})`}
                                disabled={updating || value.demo_locked}
                                title={value.demo_locked ? "Demo reports cannot be deleted" : "Delete report"}
                                onClick={() => void deleteReport(value)}>
                                Delete
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!filteredSaved.length && (
                    <p className="empty">No reports match your filters.</p>
                  )}
                </div>
              ) : (
                <div className="empty">
                  <span className="empty-icon">↗</span>
                  <h2>No reports yet.</h2>
                  <p>
                    Start with a question about demand, suppliers, components or
                    inventory.
                  </p>
                  <button className="primary" onClick={newReport}>
                    Create your first report →
                  </button>
                </div>
              )}
            </section>
          ) : !report ? (
            <section className="empty panel">
              <span className="empty-icon">↗</span>
              <h2>Your next decision starts here.</h2>
              <p>Select the analyses above and run your first report.</p>
              <span className="subtle">
                Historical baselines + clearly labeled synthetic factory data.
              </span>
            </section>
          ) : (
            <div
              aria-busy={busy}
              className={busy ? "report-content fading" : "report-content"}
            >
              <div className="report-toolbar">
                <div>
                  <p className="eyebrow">
                    PLANNING REPORT{" "}
                    <span className="report-id">#{report.id.slice(0, 8)}</span>
                  </p>
                  <h2>{report.name}</h2>
                  <p className="report-period">
                    Factory {report.site} · {day(report.planning_date)} –{" "}
                    {day(report.end_date)} · {report.config.horizon_days} days
                  </p>
                </div>
                <label>
                  Saved runs
                  <select
                    aria-label="Saved runs"
                    value={report.id}
                    disabled={updating}
                    onChange={(event) => void load(event.target.value)}
                  >
                    {!saved.some((value) => value.id === report.id) && (
                      <option value={report.id}>{report.name}</option>
                    )}
                    {saved.map((value) => (
                      <option key={value.id} value={value.id}>
                        {value.name} · {value.site} ·{" "}
                        {value.config.horizon_days}d · {value.id.slice(0, 5)}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <aside className="notice">
                <strong>Demo planning</strong>
                <span>
                  Planning date is {day(report.planning_date)}. {report.history_provenance || "Based on the supplied 2020–2023 synthetic history."} Forecasts use statistical baselines. BOMs,
                  current inventory, costs and customer orders are mock data.
                </span>
              </aside>
              {report.is_stale && (
                <div className="error" role="alert">
                  Supply, source data, or planning assumptions have changed since this snapshot. Run a new
                  report before approving more purchases.
                  <button
                    disabled={updating}
                    onClick={() => void run(report.config, report.id)}
                  >
                    Recalculate
                  </button>
                </div>
              )}
              <section className="metrics" aria-label="Report summary">
                {[
                  [
                    "Forecast demand",
                    show("demand")
                      ? number(report.summary.forecast_units)
                      : "Not selected",
                    `tractor units · ${report.config.model || "all models"}`,
                  ],
                  [
                    "Current inventory value",
                    money(report.summary.inventory_value),
                    "on-hand component stock · mock",
                  ],
                  [
                    "Components at risk",
                    number(report.summary.components_at_risk),
                    "projected shortfall · whole factory",
                  ],
                  [
                    "Recommended purchases",
                    show("inventory")
                      ? money(report.summary.recommended_purchase_cost)
                      : "Not selected",
                    "proposed spend before decisions",
                  ],
                ].map(([label, value, detail]) => (
                  <article className="metric" key={label}>
                    <p>{label}</p>
                    <strong>{value}</strong>
                    <span>{detail}</span>
                  </article>
                ))}
              </section>
              <ReportSummary key={report.id} reportId={report.id} initialSummary={report.ai_summary} />
              <nav className="section-nav" aria-label="Report sections">
                {show("demand") && <a href="#forecast">Forecast</a>}
                {(show("supply") || show("failure")) && (
                  <a href="#risk">Supplier & component risk</a>
                )}
                <a href="#inventory">Current inventory</a>
                {show("inventory") && (
                  <a href="#recommendations">Recommendations</a>
                )}
                <a href="#orders">Customer orders</a>
              </nav>
              {show("demand") && (
                <section className="panel" id="forecast">
                  <div className="panel-heading">
                    <div>
                      <h2>Demand forecast</h2>
                      <p>
                        {report.forecast.method} ·{" "}
                        {report.config.model || "All tractor models"} ·{" "}
                        {report.config.demand_change_pct > 0 ? "+" : ""}
                        {report.config.demand_change_pct}% scenario change
                      </p>
                    </div>
                    <div className="legends">
                      <span>
                        <i />
                        History
                      </span>
                      <span>
                        <i className="orange" />
                        Forecast
                      </span>
                    </div>
                  </div>
                  <DemandChart forecast={report.forecast} />
                  <details className="method">
                    <summary>Forecast evaluation & model breakdown</summary>
                    <p>
                      {report.forecast.evaluation} Scenario changes are not
                      applied to holdout metrics.
                    </p>
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th scope="col">Model</th>
                            <th scope="col">Forecast units</th>
                            <th scope="col">Baseline units/day</th>
                            <th scope="col">Holdout MAE/week</th>
                            <th scope="col">Holdout WAPE</th>
                          </tr>
                        </thead>
                        <tbody>
                          {report.forecast.models.map((model) => (
                            <tr key={model.model}>
                              <td>{model.model}</td>
                              <td>{number(model.predicted_units)}</td>
                              <td>{model.daily_units}</td>
                              <td>{model.holdout_mae}</td>
                              <td>{model.holdout_wape_pct}%</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                </section>
              )}
              {(show("supply") || show("failure")) && (
                <section className="panel" id="risk">
                  <div className="panel-heading">
                    <div>
                      <h2>Supplier & component risk</h2>
                      <p>
                        Historical mean estimates from{" "}
                        {number(report.source_rows)} observations at the{" "}
                        {report.site}
                        factory
                      </p>
                    </div>
                    <Pill tone="amber">Baseline estimates</Pill>
                  </div>
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th scope="col">Supplier</th>
                          <th scope="col">Observations</th>
                          {show("supply") && (
                            <>
                              <th scope="col">Expected extra delay</th>
                              <th scope="col">Historical p90 delay</th>
                            </>
                          )}
                          {show("failure") && (
                            <th scope="col">Expected defect-rate proxy</th>
                          )}
                          <th scope="col">Latest observation</th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.suppliers.map((supplier) => (
                          <tr key={supplier.supplier}>
                            <td>
                              <strong>{supplier.supplier}</strong>
                            </td>
                            <td>{number(supplier.observations)}</td>
                            {show("supply") && (
                              <>
                                <td>
                                  <Pill tone="amber">
                                    {supplier.expected_delay_days} days
                                  </Pill>
                                </td>
                                <td>
                                  {supplier.historical_p90_delay_days} days
                                </td>
                              </>
                            )}
                            {show("failure") && (
                              <td>{supplier.failure_rate_pct}%</td>
                            )}
                            <td>
                              {day(supplier.last_observed)}
                              {supplier.stale && <Pill tone="red">Stale</Pill>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="footnote">
                    Delay is additional to the supplier’s assumed base lead
                    time. Failure rates are supplier/model aggregate proxies,
                    not predictions that an individual component will fail.
                  </p>
                </section>
              )}
              {show("failure") && (
                <section className="panel" id="component-risk">
                  <div className="panel-heading">
                    <div>
                      <h2>Component failure exposure</h2>
                      <p>
                        Mock component-to-supplier mapping, combined with
                        historical supplier rate estimates
                      </p>
                    </div>
                    <Pill tone="amber">Aggregate proxy</Pill>
                  </div>
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th scope="col">Component</th>
                          <th scope="col">Eligible suppliers</th>
                          <th scope="col">Expected defect-rate proxy</th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.inventory.map((item) => (
                          <tr key={item.id}>
                            <td>
                              <strong>{item.name}</strong>
                            </td>
                            <td>
                              {item.eligible_suppliers?.join(", ") ||
                                "Not recorded in this snapshot"}
                            </td>
                            <td>
                              {item.failure_rate_proxy_pct === undefined
                                ? "—"
                                : `${item.failure_rate_proxy_pct}%`}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="footnote">
                    Unweighted average of eligible suppliers’ historical mean
                    failure rates. The CSV has no component IDs or exposure
                    counts; this does not predict individual part failures or
                    field reliability.
                  </p>
                </section>
              )}
              <section className="panel" id="inventory">
                <div className="panel-heading">
                  <div>
                    <h2>Current inventory</h2>
                    <p>
                      Factory {report.site} · all models · physical stock is
                      separate from reservations and planned receipts
                    </p>
                  </div>
                  <Pill>Mock operational data</Pill>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Component</th>
                        <th scope="col">On hand</th>
                        <th scope="col">Reserved</th>
                        <th scope="col">Available</th>
                        <th scope="col">Incoming</th>
                        <th scope="col">Coverage</th>
                        <th scope="col">Projected shortfall</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.inventory.map((item) => (
                        <tr key={item.id}>
                          <td>
                            <strong>{item.name}</strong>
                          </td>
                          <td>{number(item.on_hand)}</td>
                          <td>{number(item.reserved)}</td>
                          <td>{number(item.available)}</td>
                          <td>{number(item.incoming_units)}</td>
                          <td>{item.coverage_days} days</td>
                          <td>
                            <Pill
                              tone={item.projected_shortage ? "red" : "green"}
                            >
                              {item.projected_shortage
                                ? `${number(item.projected_shortage)} units`
                                : "Covered"}
                            </Pill>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="footnote">
                  Coverage uses available stock divided by average projected
                  daily component requirements. Incoming supply and shortfalls
                  reflect the saved snapshot; rerun after approving purchases.
                </p>
              </section>
              {show("inventory") && (
                <section className="panel" id="recommendations">
                  <div className="panel-heading">
                    <div>
                      <h2>Inventory recommendations</h2>
                      <p>
                        Compare suppliers and shipping, then approve or deny
                        each proposed purchase.
                      </p>
                    </div>
                    <Pill>
                      {
                        report.recommendations.filter(
                          (item) => item.status === "pending",
                        ).length
                      }{" "}
                      pending
                    </Pill>
                  </div>
                  {!report.recommendations.length && (
                    <div className="empty">
                      <h3>No new purchases recommended.</h3>
                      <p>
                        Current stock and planned receipts cover requirements,
                        or no tested purchase improves modeled cost.
                      </p>
                    </div>
                  )}
                  <div className="recommendation-list">
                    {report.recommendations.map((item) => (
                      <article className="recommendation" key={item.id}>
                        <div className="recommendation-heading">
                          <div>
                            <span className="eyebrow">REPLENISHMENT</span>
                            <h3>{item.component}</h3>
                          </div>
                          <Pill
                            tone={
                              item.status === "approved"
                                ? "green"
                                : item.status === "denied"
                                  ? "red"
                                  : "amber"
                            }
                          >
                            {item.status}
                          </Pill>
                        </div>
                        <div className="recommendation-facts">
                          <div>
                            <span>Order quantity</span>
                            <strong>{number(item.quantity)} units</strong>
                          </div>
                          <div>
                            <span>Supplier / shipping</span>
                            <strong>
                              {item.supplier} · {item.shipping}
                            </strong>
                          </div>
                          <div>
                            <span>Expected arrival</span>
                            <strong>
                              {item.arrival_date ? day(item.arrival_date) : "—"}
                            </strong>
                          </div>
                          <div>
                            <span>Purchase cost</span>
                            <strong>{money(item.purchase_cost)}</strong>
                          </div>
                        </div>
                        <p className="reason">{item.reason}</p>
                        <div className="recommendation-bottom">
                          <span>
                            {number(item.avoided_shortage_units)} component
                            shortage units avoided
                            {item.shortage_units > 0
                              ? ` · ${number(item.shortage_units)} still at risk`
                              : ""}
                            {item.order_id ? ` · ${item.order_id}` : ""}
                          </span>
                          {item.status === "pending" && (
                            <div className="decision-actions">
                              <button
                                className="secondary"
                                disabled={updating}
                                onClick={() => void decide(item.id, "denied")}
                              >
                                Deny
                              </button>
                              <button
                                className="primary"
                                disabled={updating || report.is_stale}
                                onClick={() => void decide(item.id, "approved")}
                              >
                                {decisionBusy === item.id
                                  ? "Saving…"
                                  : "Approve"}
                              </button>
                            </div>
                          )}
                        </div>
                        <details className="method">
                          <summary>
                            Why this option? Compare {item.candidates.length}{" "}
                            choices
                          </summary>
                          <div className="table-scroll">
                            <table>
                              <thead>
                                <tr>
                                  <th scope="col">Supplier / shipping</th>
                                  <th scope="col">Order units</th>
                                  <th scope="col">Purchase cost</th>
                                  <th scope="col">Shortage units</th>
                                  <th scope="col">Modeled total cost</th>
                                </tr>
                              </thead>
                              <tbody>
                                {item.candidates.map((candidate, index) => (
                                  <tr key={index}>
                                    <td>
                                      {candidate.supplier || "Current stock"} ·{" "}
                                      {candidate.shipping}
                                      {index === 0 && " ✓"}
                                    </td>
                                    <td>{number(candidate.quantity)}</td>
                                    <td>{money(candidate.purchase_cost)}</td>
                                    <td>{number(candidate.shortage_units)}</td>
                                    <td>{money(candidate.modeled_cost)}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                          <p className="footnote">
                            Total cost = purchase + holding + shortage penalties
                            under the documented demo assumptions. These are
                            modeled tradeoffs, not measured savings.
                          </p>
                        </details>
                      </article>
                    ))}
                  </div>
                </section>
              )}
              <section className="panel" id="orders">
                <div className="panel-heading">
                  <div>
                    <h2>Customer orders</h2>
                    <p>
                      Orders due within this report horizon ·{" "}
                      {report.config.model || "all tractor models"}
                    </p>
                  </div>
                  <Pill>Mock customer orders</Pill>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Order</th>
                        <th scope="col">Customer</th>
                        <th scope="col">Model</th>
                        <th scope="col">Quantity</th>
                        <th scope="col">Due date</th>
                        <th scope="col">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.customer_orders.map((order) => (
                        <tr key={order.id}>
                          <td>{order.id}</td>
                          <td>
                            <strong>{order.customer}</strong>
                          </td>
                          <td>{order.model}</td>
                          <td>{number(order.quantity)}</td>
                          <td>{day(order.due_date)}</td>
                          <td>
                            <Pill>{order.status}</Pill>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!report.customer_orders.length && (
                  <p className="empty">
                    No customer orders due within this selection.
                  </p>
                )}
              </section>
              {report.planned_orders.length > 0 && (
                <section className="panel">
                  <div className="panel-heading">
                    <div>
                      <h2>Approved planned orders</h2>
                      <p>
                        Internal purchase drafts · approval does not increase
                        physical stock
                      </p>
                    </div>
                    <Pill tone="green">Saved in PostgreSQL</Pill>
                  </div>
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th scope="col">Order</th>
                          <th scope="col">Component</th>
                          <th scope="col">Supplier</th>
                          <th scope="col">Quantity</th>
                          <th scope="col">Expected arrival</th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.planned_orders.map((order) => (
                          <tr key={order.id}>
                            <td>{order.id}</td>
                            <td>
                              {report.inventory.find(
                                (item) => item.id === order.component_id,
                              )?.name || order.component_id}
                            </td>
                            <td>{order.supplier}</td>
                            <td>{number(order.quantity)}</td>
                            <td>{day(order.expected_date)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}
              <details className="panel assumptions">
                <summary>
                  Data sources, calculation assumptions & limitations
                </summary>
                <ul>
                  {report.assumptions.map((assumption) => (
                    <li key={assumption}>{assumption}</li>
                  ))}
                </ul>
              </details>
              <footer>
                <span>
                  Saved {savedTime(report.created_at)} · Report #
                  {report.id.slice(0, 8)}
                </span>
                <span>
                  Historical replay · {report.site} factory · baseline
                  forecasting
                </span>
              </footer>
            </div>
          )}
        </main>
      </div>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
