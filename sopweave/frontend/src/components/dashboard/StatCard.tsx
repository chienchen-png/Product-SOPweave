export default function StatCard({label, value, sub, hint}: {label: string; value: string; sub: string; hint?: string}) {
  return <article className="dashboard-stat" aria-label={label} title={hint}>
    <h2 className="text-sm font-normal text-zinc-500">{label}</h2>
    <p className="mt-2 text-[26px] leading-tight font-semibold tracking-tight text-zinc-900 tabular-nums">{value}</p>
    <p className="mt-2 text-xs text-zinc-500">{sub}</p>
  </article>;
}
