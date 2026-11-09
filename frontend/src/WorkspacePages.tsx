import { useState } from "react";

export type Page =
  | "home"
  | "inventory"
  | "customer-orders"
  | "proposed-orders"
  | "reports";
export const PAGES: {
  id: Page;
  name: string;
  icon: string;
  subtitle: string;
}[] = [
  {
    id: "home",
    name: "Home",
    icon: "⌂",
    subtitle: "The next decisions for your factory, in one place.",
  },
  {
    id: "inventory",
    name: "Inventory",
    icon: "▦",
    subtitle: "Physical stock, reservations and approved incoming supply.",
  },
  {
    id: "customer-orders",
    name: "Customer Orders",
    icon: "▤",
    subtitle: "What your customers need, and when they need it.",
  },
  {
    id: "proposed-orders",
    name: "Proposed Inventory Orders",
    icon: "⇥",
    subtitle: "Approved recommendations collected as internal purchase plans.",
  },
  {
    id: "reports",
    name: "Reports",
    icon: "▥",
    subtitle:
      "Saved decisions, assumptions and results. Pick up where you left off.",
  },
];
export type Workspace = {
  site: string;
  factory_count?: number;
  planning_by_factory?: {
    site: string;
    latest_report: Workspace["latest_report"];
  }[];
  planning_date: string;
  provenance: string;
  inventory: {
    component_id: string;
    site: string;
    name: string;
    on_hand: number;
    reserved: number;
    available: number;
    incoming_units: number;
    unit_cost: number;
  }[];
  customer_orders: {
    id: string;
    site: string;
    customer: string;
    model: string;
    quantity: number;
    due_date: string;
    due_in_days: number;
    status: string;
  }[];
  approved_orders: {
    id: string;
    site: string;
    component_id: string;
    component_name: string;
    supplier: string;
    quantity: number;
    expected_date: string;
    purchase_cost: number;
    shipping: string;
    report_id: string;
    report_name: string;
    report_deleted: boolean;
    approved_at: string;
  }[];
  summary: {
    inventory_value: number;
    customer_order_count: number;
    due_30_days: number;
    approved_order_count: number;
  };
  latest_report: null | {
    id: string;
    name: string;
    created_at: string;
    is_stale: boolean;
    end_date: string;
    config: {
      model?: string;
      horizon_days: number;
      demand_change_pct: number;
      extra_delay_days: number;
    };
    needs: {
      id: string;
      component: string;
      quantity: number;
      purchase_cost: number;
      coverage_days: number;
      supplier: string;
      shipping: string;
    }[];
  };
};
const n = (value: number) => new Intl.NumberFormat("en-US").format(value);
const dollars = (value: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
const date = (value: string) =>
  new Date(`${value}T00:00:00`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });

function SupplyFlow() {
  return (
    <div
      className="supply-flow"
      role="img"
      aria-label="Animated illustration of parts moving from suppliers through the factory to customer orders"
    >
      <div className="flow-grid" />
      <svg viewBox="0 0 520 220" aria-hidden="true">
        <path className="flow-track" d="M82 112H255H438" />
        <path
          className="flow-track branch"
          d="M82 64C160 64 165 112 255 112M82 165C160 165 165 112 255 112"
        />
        <circle className="moving-part path-one" r="4" />
        <circle className="moving-part path-two" r="4" />
        <circle className="moving-part path-three" r="4" />
        <g className="flow-node">
          <rect x="53" y="88" width="50" height="48" rx="9" />
          <path d="M65 101h25v22H65zM65 101l12 7 13-7m-13 7v15" />
        </g>
        <g className="flow-node">
          <rect x="230" y="82" width="60" height="60" rx="12" />
          <path d="M242 129V105l12 7v-10l12 7V95h10v34zM248 120h3m9 0h3m8 0h3" />
        </g>
        <g className="flow-node">
          <rect x="412" y="88" width="50" height="48" rx="9" />
          <path d="M423 103h25v23h-25zm6-6h13v6h-13m0 9h13m-13 6h9" />
        </g>
        <text x="78" y="162">
          SUPPLIERS
        </text>
        <text x="260" y="169">
          YOUR FACTORY
        </text>
        <text x="437" y="162">
          CUSTOMER ORDERS
        </text>
      </svg>
      <span className="flow-caption">
        <span className="site-dot" /> A connected view of your supply chain
      </span>
    </div>
  );
}

export function WorkspacePages({
  page,
  data,
  onNavigate,
  onOpenReport,
}: {
  page: Exclude<Page, "reports">;
  data: Workspace;
  onNavigate: (page: Page) => void;
  onOpenReport: (id: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const latest = data.latest_report;
  const allFactories = data.site === "ALL";
  const scopeLabel = allFactories ? "All factories" : `Factory ${data.site}`;
  const freshPlanning = (data.planning_by_factory || []).filter(
    (item) => item.latest_report && !item.latest_report.is_stale,
  );
  const pendingTotal = freshPlanning.length
    ? n(
        freshPlanning.reduce(
          (total, item) => total + (item.latest_report?.needs.length || 0),
          0,
        ),
      )
    : "—";
  const match = (value: string) =>
    value.toLowerCase().includes(search.toLowerCase());
  const context = (
    <aside className="notice">
      <strong>{scopeLabel}</strong>
      <span>
        Planning date: {date(data.planning_date)} · Synthetic demo with generated
        2024–2026 history and mock operations. Approved orders are purchase plans; stock has not been
        received. {allFactories && "Stock remains separate for each factory."}
      </span>
    </aside>
  );
  if (page === "home")
    return (
      <>
        <section className="home-hero">
          <div className="hero-copy">
            <p className="eyebrow">PARTS. PRODUCTION. PEOPLE.</p>
            <h2>
              Keep the right
              <br />
              parts moving.
            </h2>
            <p>
              A clear view of what you have, what customers need, and which
              purchases are ready to move forward.
            </p>
            <button className="secondary" onClick={() => onNavigate("reports")}>
              View reports →
            </button>
          </div>
          <SupplyFlow />
        </section>
        {context}
        <section className="metrics" aria-label="Factory summary">
          {[
            [
              "Inventory value",
              dollars(data.summary.inventory_value),
              "physical stock · mock opening balances",
            ],
            [
              "Due in 30 days",
              n(data.summary.due_30_days),
              "customer orders · simulated planning date",
            ],
            [
              "Needs approval",
              allFactories
                ? pendingTotal
                : latest && !latest.is_stale
                  ? n(latest.needs.length)
                  : "—",
              allFactories
                ? `from ${freshPlanning.length} current reports; other factories unassessed`
                : latest?.is_stale
                  ? "latest inventory report needs refresh"
                  : "from latest inventory report",
            ],
            [
              "Approved purchases",
              n(data.summary.approved_order_count),
              "internal plans · read-only",
            ],
          ].map(([label, value, detail]) => (
            <article className="metric" key={label}>
              <p>{label}</p>
              <strong>{value}</strong>
              <span>{detail}</span>
            </article>
          ))}
        </section>
        <section className="panel attention">
          <div className="panel-heading">
            <div>
              <h2>Needs attention</h2>
              <p>
                {allFactories
                  ? "Latest planning context for each factory; scopes and assumptions remain separate."
                  : latest
                    ? `Based on “${latest.name}” · ${latest.config.model || "All models"} · ${latest.config.horizon_days} days · ${latest.config.demand_change_pct}% demand scenario · +${latest.config.extra_delay_days} days supplier delay`
                    : "Run an inventory report to identify component needs."}
              </p>
            </div>
            {latest && (
              <button
                className="secondary"
                onClick={() => onOpenReport(latest.id)}
              >
                Open report →
              </button>
            )}
          </div>
          {allFactories ? (
            <div className="need-grid">
              {data.planning_by_factory?.map((item) => (
                <article className="need-card" key={item.site}>
                  <span className="pill">Factory {item.site}</span>
                  <h3>
                    {!item.latest_report
                      ? "No inventory report yet"
                      : item.latest_report.is_stale
                        ? "Report needs a refresh"
                        : `${item.latest_report.needs.length} pending recommendations`}
                  </h3>
                  <p>
                    {item.latest_report
                      ? `${item.latest_report.name} · ${item.latest_report.config.model || "All models"} · ${item.latest_report.config.horizon_days} days · ${item.latest_report.config.demand_change_pct}% demand scenario · +${item.latest_report.config.extra_delay_days} days delay`
                      : "Open Reports to assess this factory’s inventory needs."}
                  </p>
                  {item.latest_report ? (
                    <button
                      className="text-button"
                      onClick={() => onOpenReport(item.latest_report!.id)}
                    >
                      Open factory report →
                    </button>
                  ) : (
                    <button
                      className="text-button"
                      onClick={() => onNavigate("reports")}
                    >
                      View reports →
                    </button>
                  )}
                </article>
              ))}
            </div>
          ) : !latest ? (
            <div className="empty compact">
              <h3>Your first planning insight starts with a report.</h3>
              <p>
                Choose inventory recommendations on the Reports page to see what
                is needed and why.
              </p>
              <button
                className="secondary"
                onClick={() => onNavigate("reports")}
              >
                Go to reports
              </button>
            </div>
          ) : latest.is_stale ? (
            <div className="notice">
              <strong>Report needs a refresh</strong>
              <span>
                Supply, source data, or planning assumptions changed after this snapshot. Open the report and
                recalculate before relying on its recommendations.
              </span>
            </div>
          ) : latest.needs.length ? (
            <div className="need-grid">
              {latest.needs.slice(0, 6).map((item) => (
                <article className="need-card" key={item.id}>
                  <span className="pill amber">
                    {item.coverage_days} days of stock
                  </span>
                  <h3>{item.component}</h3>
                  <p>
                    Proposed purchase: <strong>{n(item.quantity)} units</strong>
                  </p>
                  <span>
                    {item.supplier} · {item.shipping} ·{" "}
                    {dollars(item.purchase_cost)}
                  </span>
                  <button
                    className="text-button"
                    onClick={() => onOpenReport(latest.id)}
                  >
                    Review recommendation →
                  </button>
                </article>
              ))}
            </div>
          ) : (
            <div className="empty compact">
              <h3>No pending purchase recommendations.</h3>
              <p>
                Review approved plans or run an updated inventory report when
                assumptions change.
              </p>
            </div>
          )}
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Upcoming customer orders</h2>
              <p>Next deliveries for the selected scope</p>
            </div>
            <button
              className="secondary"
              onClick={() => onNavigate("customer-orders")}
            >
              View all orders →
            </button>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Customer</th>
                  <th scope="col">Factory</th>
                  <th scope="col">Model</th>
                  <th scope="col">Quantity</th>
                  <th scope="col">Due date</th>
                </tr>
              </thead>
              <tbody>
                {data.customer_orders.slice(0, 4).map((order) => (
                  <tr key={order.id}>
                    <td>
                      <strong>{order.customer}</strong>
                    </td>
                    <td>{order.site}</td>
                    <td>{order.model}</td>
                    <td>{n(order.quantity)}</td>
                    <td>{date(order.due_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </>
    );
  if (page === "inventory")
    return (
      <>
        {context}
        <section className="metrics" aria-label="Inventory summary">
          <article className="metric">
            <p>On-hand value</p>
            <strong>{dollars(data.summary.inventory_value)}</strong>
            <span>Physical stock · excludes unreceived orders</span>
          </article>
          <article className="metric">
            <p>Component stock positions</p>
            <strong>{data.inventory.length}</strong>
            <span>{scopeLabel} · separate stock ledgers</span>
          </article>
          <article className="metric">
            <p>Approved incoming orders</p>
            <strong>{data.summary.approved_order_count}</strong>
            <span>Planned supply · stock remains unchanged</span>
          </article>
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Component inventory</h2>
              <p>
                On hand − reserved = available. Incoming supply is shown
                separately.
              </p>
            </div>
            <input
              className="search"
              aria-label="Search inventory"
              placeholder="Search components…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Component</th>
                  <th scope="col">Factory</th>
                  <th scope="col">On hand</th>
                  <th scope="col">Reserved</th>
                  <th scope="col">Available</th>
                  <th scope="col">Approved incoming</th>
                  <th scope="col">Unit cost</th>
                  <th scope="col">Stock value</th>
                </tr>
              </thead>
              <tbody>
                {data.inventory
                  .filter((item) => match(item.name))
                  .map((item) => (
                    <tr key={`${item.site}-${item.component_id}`}>
                      <td>
                        <strong>{item.name}</strong>
                      </td>
                      <td>{item.site}</td>
                      <td>{n(item.on_hand)}</td>
                      <td>{n(item.reserved)}</td>
                      <td>{n(item.available)}</td>
                      <td>{n(item.incoming_units)}</td>
                      <td>{dollars(item.unit_cost)}</td>
                      <td>{dollars(item.on_hand * item.unit_cost)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          {!data.inventory.some((item) => match(item.name)) && (
            <p className="empty compact">No components match your search.</p>
          )}
          <p className="footnote">
            To assess future shortages, review an inventory report. Physical
            balances are mock data; this page does not infer stock from
            historical CSV inventory observations.
          </p>
        </section>
      </>
    );
  if (page === "customer-orders") {
    const rows = data.customer_orders.filter(
      (order) =>
        match(`${order.id} ${order.customer} ${order.model}`) &&
        (!status || order.status === status),
    );
    return (
      <>
        {context}
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>
                Customer orders <span className="subtle">({rows.length})</span>
              </h2>
              <p>
                All mock customer orders for {scopeLabel.toLowerCase()},
                independent of report horizons.
              </p>
            </div>
            <div className="page-filters">
              <input
                className="search"
                aria-label="Search customer orders"
                placeholder="Customer, model or order…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              <select
                aria-label="Customer order status"
                value={status}
                onChange={(event) => setStatus(event.target.value)}
              >
                <option value="">All statuses</option>
                {[
                  ...new Set(data.customer_orders.map((order) => order.status)),
                ].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Order</th>
                  <th scope="col">Customer</th>
                  <th scope="col">Factory</th>
                  <th scope="col">Model</th>
                  <th scope="col">Quantity</th>
                  <th scope="col">Due date</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((order) => (
                  <tr key={order.id}>
                    <td>{order.id}</td>
                    <td>
                      <strong>{order.customer}</strong>
                    </td>
                    <td>{order.site}</td>
                    <td>{order.model}</td>
                    <td>{n(order.quantity)}</td>
                    <td>{date(order.due_date)}</td>
                    <td>
                      <span className="pill">{order.status}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!rows.length && (
            <p className="empty compact">
              No customer orders match your filters.
            </p>
          )}
        </section>
      </>
    );
  }
  const rows = data.approved_orders.filter((order) =>
    match(`${order.id} ${order.component_name} ${order.supplier}`),
  );
  return (
    <>
      {context}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>
              Approved inventory orders{" "}
              <span className="subtle">({rows.length})</span>
            </h2>
            <p>
              Every approved recommendation in the selected scope. These plans
              are read-only and have not been sent to suppliers.
            </p>
          </div>
          <input
            className="search"
            aria-label="Search proposed orders"
            placeholder="Component, supplier or order…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        {rows.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Order</th>
                  <th scope="col">Component</th>
                  <th scope="col">Factory</th>
                  <th scope="col">Supplier / shipping</th>
                  <th scope="col">Quantity</th>
                  <th scope="col">Expected arrival</th>
                  <th scope="col">Purchase cost</th>
                  <th scope="col">Status</th>
                  <th scope="col">Source report</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((order) => (
                  <tr key={order.id}>
                    <td>{order.id}</td>
                    <td>
                      <strong>{order.component_name}</strong>
                    </td>
                    <td>{order.site}</td>
                    <td>
                      {order.supplier} · {order.shipping}
                    </td>
                    <td>{n(order.quantity)}</td>
                    <td>{date(order.expected_date)}</td>
                    <td>{dollars(order.purchase_cost)}</td>
                    <td>
                      <span className="pill green">Approved · planned</span>
                    </td>
                    <td>
                      {order.report_deleted ? (
                        <span>{order.report_name} <span className="subtle">(deleted)</span></span>
                      ) : (
                      <button
                        className="text-button"
                        onClick={() => onOpenReport(order.report_id)}
                      >
                        {order.report_name} ↗
                      </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty compact">
            <h3>
              {search
                ? "No orders match your search."
                : "No approved inventory orders yet."}
            </h3>
            <p>
              Approve an inventory recommendation in a report to add it here.
            </p>
            <button className="secondary" onClick={() => onNavigate("reports")}>
              View reports
            </button>
          </div>
        )}
      </section>
    </>
  );
}
