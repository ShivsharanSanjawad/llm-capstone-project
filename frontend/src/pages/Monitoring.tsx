import { api } from '../services/api';
import {
  PageTitle,
  useLoad,
  Loading,
  ErrorNotice,
  Badge,
  Empty,
} from '../components/ui';
import { dateTimeLabel } from '../services/data';
/** Small costs need more decimals than cents: $0.0018, not $0.00. */
function usd(value: number) {
  if (value === 0) return '$0.00';
  if (value < 0.0001) return '<$0.0001';
  return '$' + (value < 1 ? value.toFixed(4) : value.toFixed(2));
}
export default function Monitoring() {
  const data = useLoad(
    async () => ({
      health: await api.health(),
      stats: await api.stats(),
    }),
    [],
    'monitoring'
  );
  return (
    <>
      <PageTitle
        title="Behind every meeting record"
        description="Provider health, processing performance and transparent usage."
        action={<button onClick={data.reload}>Refresh metrics</button>}
      />
      {data.loading ? (
        <Loading />
      ) : data.error ? (
        <ErrorNotice message={data.error} retry={data.reload} />
      ) : (
        data.value &&
        (() => {
          const { health, stats: s } = data.value;
          return (
            <>
              <section className="provider-line">
                <div>
                  <span
                    className={
                      'connection-dot ' +
                      (health.database_connected ? 'connected' : '')
                    }
                  />
                  <strong>
                    {health.provider || 'Provider not configured'}
                  </strong>
                  <span>{health.model || 'No model selected'}</span>
                </div>
                <Badge>
                  {health.configured ? 'Configured' : 'Missing API key'}
                </Badge>
                <span>
                  Database:{' '}
                  {health.database_connected
                    ? 'Atlas connected'
                    : 'Unavailable'}
                </span>
              </section>
              <div className="stats-strip">
                <div>
                  <span>Generation requests</span>
                  <strong>{s.requests}</strong>
                </div>
                <div>
                  <span>Successful</span>
                  <strong>{s.succeeded}</strong>
                </div>
                <div>
                  <span>Failed</span>
                  <strong>{s.failed}</strong>
                </div>
                <div>
                  <span>Average processing</span>
                  <strong>
                    {s.average_duration_seconds === null
                      ? '?'
                      : s.average_duration_seconds.toFixed(1) + 's'}
                  </strong>
                </div>
                <div>
                  <span>LLM calls</span>
                  <strong>{s.llm_calls}</strong>
                </div>
                <div>
                  <span>Retries</span>
                  <strong>{s.retries}</strong>
                </div>
              </div>
              <div className="two-column">
                <section className="panel metric-panel">
                  <p className="eyebrow">PROVIDER-REPORTED USAGE</p>
                  <h2>
                    {s.requests
                      ? (s.input_tokens + s.output_tokens).toLocaleString()
                      : '?'}{' '}
                    <small>tokens reported</small>
                  </h2>
                  <p>
                    {s.input_tokens.toLocaleString()} input ?{' '}
                    {s.output_tokens.toLocaleString()} output
                  </p>
                  <p className="muted">
                    {s.usage_complete
                      ? 'Provider usage was available for all recorded attempts.'
                      : 'Partial measurement: some attempts have no reported usage. Byte-based estimates are shown separately in request details.'}
                  </p>
                </section>
                <section className="panel metric-panel">
                  <p className="eyebrow">ESTIMATED API COST</p>
                  <h2>
                    {s.estimated_cost_usd === null
                      ? 'Unavailable'
                      : usd(s.estimated_cost_usd)}
                  </h2>
                  <p className="muted">
                    {s.estimated_cost_usd === null
                      ? 'No recorded request uses a model that is in the price list. Add its rates with a pricing file.'
                      : 'What this usage would cost at paid-tier list prices. Free tiers bill $0, and cached-input discounts are not applied.'}
                  </p>
                  {s.estimated_cost_usd !== null && (
                    <p className="muted small">
                      {s.cost_priced_requests} of {s.requests} requests priced
                      {s.cost_estimated_requests
                        ? `; ${s.cost_estimated_requests} estimated from transcript size because the provider reported no usage`
                        : ''}
                      {s.cost_unpriced_requests
                        ? `; ${s.cost_unpriced_requests} left out (model not in the price list)`
                        : ''}
                      .
                    </p>
                  )}
                </section>
              </div>
              <section className="panel">
                <div className="section-title">
                  <h2>Processing ledger</h2>
                  <span className="muted small">
                    {s.scope} ? newest 50 shown
                  </span>
                </div>
                {s.recent.length ? (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Request / meeting</th>
                          <th>Provider / model</th>
                          <th>Status</th>
                          <th>Duration</th>
                          <th>Calls / retries</th>
                          <th>Review flags</th>
                          <th>Details</th>
                        </tr>
                      </thead>
                      <tbody>
                        {s.recent.map((log) => (
                          <tr key={log.request_id}>
                            <td>
                              <strong>{log.meeting_id}</strong>
                              <small>
                                {dateTimeLabel(log.started_at)} ?{' '}
                                {log.request_id.slice(0, 8)}
                              </small>
                            </td>
                            <td>
                              {log.provider}
                              <small>{log.model}</small>
                            </td>
                            <td>
                              <Badge>{log.status}</Badge>
                              {log.error_type && (
                                <small>{log.error_type}</small>
                              )}
                            </td>
                            <td>{log.duration_seconds.toFixed(2)}s</td>
                            <td>
                              {log.calls} / {log.retries}
                            </td>
                            <td>{log.verification?.flag_count ?? '?'}</td>
                            <td>
                              <details>
                                <summary>Usage</summary>
                                <p>
                                  Reported: {log.input_tokens} input /{' '}
                                  {log.output_tokens} output
                                </p>
                                <p>
                                  Estimated: {log.estimated_input_tokens} input
                                  / {log.estimated_output_tokens} output
                                </p>
                                <p>
                                  Complete usage:{' '}
                                  {log.usage_complete ? 'Yes' : 'No'}
                                </p>
                                {log.estimated_cost_usd != null && (
                                  <p>
                                    Cost at list price:{' '}
                                    {usd(log.estimated_cost_usd)}
                                    {log.cost_basis === 'estimated_tokens'
                                      ? ' (from estimated tokens)'
                                      : ' (provider-reported tokens)'}
                                    {log.pricing_source
                                      ? ` · ${log.pricing_source}`
                                      : ''}
                                  </p>
                                )}
                              </details>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <Empty title="The ledger is ready">
                    Generate your first meeting minutes to see real processing
                    metrics.
                  </Empty>
                )}
              </section>
            </>
          );
        })()
      )}
    </>
  );
}
