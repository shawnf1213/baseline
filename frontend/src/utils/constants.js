export const COURTS_BY_SURFACE = {
  Hard:  ['Australian Open','US Open','Indian Wells','Miami','Cincinnati',
          'Canada Montreal','Vienna','Basel','Rotterdam','Doha','Dubai','ATP Finals'],
  Clay:  ['Roland Garros','Monte Carlo','Madrid','Barcelona','Rome','Hamburg','Geneva','Lyon'],
  Grass: ['Wimbledon','Queens Club','Halle','Stuttgart Grass','Eastbourne'],
}

export const ATP_AVERAGES = {
  aces: 10.0, double_faults: 3.0,
  first_serve_pct: 65.0, first_serve_pts_won: 75.0,
  second_serve_pts_won: 55.0, return_first_serve_pts_won: 32.0,
  return_second_serve_pts_won: 53.0, bp_converted: 45.0, bp_saved: 65.0,
}

export const WTA_AVERAGES = {
  aces: 2.0, double_faults: 4.0,
  first_serve_pct: 62.0, first_serve_pts_won: 65.0,
  second_serve_pts_won: 50.0, return_first_serve_pts_won: 38.0,
  return_second_serve_pts_won: 56.0, bp_converted: 42.0, bp_saved: 60.0,
}

export const SURFACE_COLORS = { Hard: '#42A5F5', Clay: '#EF6C00', Grass: '#2E7D32' }

export const STAT_LABELS = {
  aces: 'Aces / Match',
  double_faults: 'Double Faults / Match',
  first_serve_pct: '1st Serve %',
  first_serve_pts_won: '1st Serve Pts Won',
  second_serve_pts_won: '2nd Serve Pts Won',
  return_first_serve_pts_won: 'Ret Pts Won (1st Srv)',
  return_second_serve_pts_won: 'Ret Pts Won (2nd Srv)',
  bp_converted: 'BP Converted %',
  bp_saved: 'BP Saved %',
}

export const fmt = (v, d = 1) => v == null ? '—' : Number(v).toFixed(d)
export const fmtPct = (v) => v == null ? '—' : `${Number(v).toFixed(0)}%`
