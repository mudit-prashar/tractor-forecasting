import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

type Metadata = { models: string[]; warehouses: string[]; start_date: string; end_date: string; dataset_checksum: string };
type Week = { week: string; demand_units: number; observations: number };
type Overview = {
  summary: { observations: number; demand_units: number; models: number; suppliers: number; start_date: string | null; end_date: string | null };
  weekly: Week[];
  suppliers: { supplier: string; observations: number; mean_delay_days: number; mean_failure_percent: number }[];
};
const number = new Intl.NumberFormat('en-US');
const date = (value: string) => new Date(`${value}T00:00:00`).toLocaleDateString('en-US', { month: 'short', year: 'numeric' });

async function get<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal });
  if (!response.ok) throw new Error(`Data request failed (${response.status}). Check the API and database.`);
  return response.json();
}

function DemandChart({ weeks }: { weeks: Week[] }) {
  const recent = weeks.slice(-26);
  if (!recent.length) return <p className="empty">No observations match these filters.</p>;
  const maximum = Math.max(...recent.map(week => week.demand_units), 1);
  const left = 64, top = 20, width = 870, height = 210;
  const points = recent.map(week => {
    const days = (Date.parse(week.week) - Date.parse(recent[0].week)) / 86400000;
    const totalDays = (Date.parse(recent[recent.length - 1].week) - Date.parse(recent[0].week)) / 86400000;
    return `${left + (totalDays ? days / totalDays : 0) * width},${top + height - week.demand_units / maximum * height}`;
  }).join(' ');
  return <>
    <svg className="chart" viewBox="0 0 960 275" role="img" aria-label="Recorded weekly demand over the most recent 26 observed weeks">
      {[0, 0.5, 1].map(fraction => <g key={fraction}>
        <line x1={left} x2={left + width} y1={top + height * fraction} y2={top + height * fraction} stroke="#e6ebe9" />
        <text x={left - 12} y={top + height * fraction + 4} textAnchor="end">{number.format(Math.round(maximum * (1 - fraction)))}</text>
      </g>)}
      <polyline points={points} fill="none" stroke="#197266" strokeWidth="3" strokeLinejoin="round" />
      <text x={left} y={265}>{date(recent[0].week)}</text>
      <text x={left + width} y={265} textAnchor="end">{date(recent[recent.length - 1].week)}</text>
    </svg>
    <p className="footnote">Tractor units · weekly sums · latest 26 observed weeks. Boundary weeks may be partial; gaps between observations do not imply zero demand.</p>
  </>;
}

function App() {
  const [metadata, setMetadata] = useState<Metadata | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [model, setModel] = useState('');
  const [warehouse, setWarehouse] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    get<Metadata>('/api/metadata', controller.signal).then(setMetadata).catch(error => {
      if (error.name !== 'AbortError') setError(error.message);
    });
    return () => controller.abort();
  }, [retry]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(''); setOverview(null);
    const query = new URLSearchParams();
    if (model) query.set('model', model);
    if (warehouse) query.set('warehouse', warehouse);
    get<Overview>(`/api/overview?${query}`, controller.signal)
      .then(setOverview)
      .catch(error => { if (error.name !== 'AbortError') setError(error.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [model, warehouse, retry]);

  const summary = overview?.summary;
  return <div className="app">
    <header className="topbar"><div className="brand"><span className="brand-icon" aria-hidden="true">T</span> Tractor <span className="brand-light">/ Supply planning</span></div><span className="badge">Historical data · v0.1</span></header>
    <main>
      <div className="heading"><div><p className="eyebrow">PLANNING WORKSPACE</p><h1>Start with the evidence.</h1><p className="subtitle">Explore demand and supplier history before making the next inventory decision.</p></div><div className="period"><span>DATA COVERAGE</span><strong>{metadata ? `${date(metadata.start_date)} – ${date(metadata.end_date)}` : 'Loading…'}</strong></div></div>
      <div className="toolbar"><div className="active-tab">Historical overview</div><div className="filters"><label>Tractor model<select value={model} onChange={event => setModel(event.target.value)} disabled={!metadata}><option value="">All models</option>{metadata?.models.map(value => <option key={value}>{value}</option>)}</select></label><label>Warehouse<select value={warehouse} onChange={event => setWarehouse(event.target.value)} disabled={!metadata}><option value="">All warehouses</option>{metadata?.warehouses.map(value => <option key={value}>{value}</option>)}</select></label></div></div>
      <aside className="notice"><strong>Source assumptions</strong><span>Demand rows are treated as additive observations. Recorded inventory is historical and has not been mapped to component stock.</span></aside>
      {error && <div role="alert" className="error">{error} <button onClick={() => setRetry(value => value + 1)}>Retry</button></div>}
      <div aria-busy={loading}>
        <section className="metrics" aria-label="Historical summary">
          {[['Recorded demand', summary ? number.format(summary.demand_units) : '—', 'tractor units · selected history'], ['Source observations', summary ? number.format(summary.observations) : '—', 'raw records preserved'], ['Tractor models', summary?.models ?? '—', 'represented in this selection'], ['Suppliers', summary?.suppliers ?? '—', 'represented in this selection']].map(([label, value, detail]) => <article className="metric" key={label}><p>{label}</p><strong>{value}</strong><span>{detail}</span></article>)}
        </section>
        <section className="panel"><div className="panel-heading"><div><h2>Demand over time</h2><p>Recorded history for the selected models and warehouses</p></div><span className="legend"><i /> Observed demand</span></div>{loading ? <p className="empty" role="status">Loading historical observations…</p> : <DemandChart weeks={overview?.weekly ?? []} />}</section>
        <section className="panel"><div className="panel-heading"><div><h2>Supplier history</h2><p>Observed averages across the selected historical records</p></div><span className="subtle">Predictions come next</span></div><div className="table-scroll"><table><thead><tr><th>Supplier</th><th>Observations</th><th>Mean recorded delay</th><th>Mean recorded failure rate</th></tr></thead><tbody>{overview?.suppliers.map(supplier => <tr key={supplier.supplier}><td><strong>{supplier.supplier}</strong></td><td>{number.format(supplier.observations)}</td><td>{Number(supplier.mean_delay_days).toFixed(1)} days</td><td>{Number(supplier.mean_failure_percent).toFixed(2)}%</td></tr>)}</tbody></table></div>{!loading && !overview?.suppliers.length && <p className="empty">No supplier observations to show.</p>}</section>
      </div>
      <footer><span>Next increment: forecast → component requirements → purchase recommendation.</span><span>Source: supplied synthetic dataset{metadata ? ` · ${metadata.dataset_checksum.slice(0, 8)}` : ''}</span></footer>
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);

