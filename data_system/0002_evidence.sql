-- Synthetic sample evidence for the MCP tool surface.
-- Not live carrier rates, not a booking, not a part release.

create table if not exists observations (
  id text primary key,
  kind text not null,
  subject text not null,
  payload jsonb not null,
  partiality jsonb not null default '[]'::jsonb,
  observed_from timestamptz,
  observed_to timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists data_stamps (
  id text primary key,
  observation_id text not null unique references observations (id),
  source_name text not null,
  content_hash text not null,
  frame text,
  units jsonb not null default '{}'::jsonb,
  uncertainty jsonb not null default '{}'::jsonb,
  review_status text not null default 'synthetic_sample',
  transformations jsonb not null default '[]'::jsonb
);

create table if not exists investigations (
  id text primary key,
  question text not null,
  status text not null,
  context jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists executions (
  id text primary key,
  investigation_id text references investigations (id),
  operation_id text not null,
  attempt text not null,
  status text not null,
  inputs jsonb not null,
  created_at timestamptz not null default now()
);

create unique index if not exists executions_op_attempt_idx
  on executions (operation_id, attempt);

create table if not exists results (
  id text primary key,
  execution_id text not null unique references executions (id),
  verification_status text not null default 'not_verified',
  data jsonb not null,
  created_at timestamptz not null default now()
);

create table if not exists relationships (
  id text primary key,
  kind text not null,
  from_id text not null,
  to_id text not null,
  note text not null default ''
);

create index if not exists relationships_from_idx on relationships (from_id);
create index if not exists relationships_to_idx on relationships (to_id);

create table if not exists candidates (
  id text primary key,
  operation_id text not null,
  note text not null,
  created_at timestamptz not null default now()
);

create table if not exists mcp_receipts (
  id text primary key,
  tool_name text not null,
  arguments jsonb not null,
  status text not null,
  created_at timestamptz not null default now()
);

insert into observations (id, kind, subject, payload, partiality, observed_from, observed_to) values
  ('obs-sha-ocean', 'rate', 'SHA–YVR ocean 40HC', '{"role":"buy","mode":"ocean","leg":1,"origin":"CNSHA","destination":"CAYVR","origin_name":"Shanghai","destination_name":"Vancouver","amount_usd":1850,"unit":"per_container","equipment":"40HC","origin_lon":121.47,"origin_lat":31.23,"destination_lon":-123.12,"destination_lat":49.28}'::jsonb, '["bunker_adjustment"]'::jsonb, '2026-11-01T00:00:00Z', '2026-11-15T00:00:00Z'),
  ('obs-yvr-rail', 'rate', 'YVR–YYZ rail', '{"role":"buy","mode":"rail","leg":2,"origin":"CAYVR","destination":"CAYYZ","origin_name":"Vancouver","destination_name":"Toronto","amount_usd":980,"unit":"per_container","equipment":"40HC","origin_lon":-123.12,"origin_lat":49.28,"destination_lon":-79.38,"destination_lat":43.65}'::jsonb, '["rail_space"]'::jsonb, '2026-11-01T00:00:00Z', '2026-11-15T00:00:00Z'),
  ('obs-yyz-dray', 'rate', 'YYZ–Mississauga dray', '{"role":"buy","mode":"road","leg":3,"origin":"CAYYZ","destination":"CAMIS","origin_name":"Toronto","destination_name":"Mississauga","amount_usd":410,"unit":"per_container","equipment":"40HC","origin_lon":-79.38,"origin_lat":43.65,"destination_lon":-79.64,"destination_lat":43.59}'::jsonb, '[]'::jsonb, '2026-11-01T00:00:00Z', '2026-11-15T00:00:00Z'),
  ('obs-sha-sell', 'rate', 'SHA–YYZ offered sell', '{"role":"sell","mode":"all-in","amount_usd":4600,"unit":"per_container","equipment":"40HC"}'::jsonb, '[]'::jsonb, '2026-11-01T00:00:00Z', '2026-11-08T00:00:00Z'),
  ('obs-yvr-space', 'capacity', 'YVR rail space', '{"role":"capacity","confirmed":false,"note":"space not confirmed for the window"}'::jsonb, '["rail_space"]'::jsonb, '2026-11-01T00:00:00Z', '2026-11-15T00:00:00Z'),
  ('obs-rtm-ocean', 'rate', 'RTM–MTL ocean 40HC', '{"role":"buy","mode":"ocean","leg":1,"origin":"NLRTM","destination":"CAMTR","origin_name":"Rotterdam","destination_name":"Montreal","amount_usd":2100,"unit":"per_container","equipment":"40HC","origin_lon":4.48,"origin_lat":51.92,"destination_lon":-73.55,"destination_lat":45.50}'::jsonb, '[]'::jsonb, '2026-11-02T00:00:00Z', '2026-11-20T00:00:00Z'),
  ('obs-mtl-road', 'rate', 'MTL–YYZ road', '{"role":"buy","mode":"road","leg":2,"origin":"CAMTR","destination":"CAYYZ","origin_name":"Montreal","destination_name":"Toronto","amount_usd":640,"unit":"per_container","equipment":"40HC","origin_lon":-73.55,"origin_lat":45.50,"destination_lon":-79.38,"destination_lat":43.65}'::jsonb, '[]'::jsonb, '2026-11-02T00:00:00Z', '2026-11-20T00:00:00Z'),
  ('obs-rtm-sell', 'rate', 'RTM–YYZ offered sell', '{"role":"sell","mode":"all-in","amount_usd":3900,"unit":"per_container","equipment":"40HC"}'::jsonb, '[]'::jsonb, '2026-11-02T00:00:00Z', '2026-11-09T00:00:00Z'),
  ('obs-mtl-space', 'capacity', 'MTL truck capacity', '{"role":"capacity","confirmed":true,"note":"one 53ft confirmed inside the window"}'::jsonb, '[]'::jsonb, '2026-11-02T00:00:00Z', '2026-11-20T00:00:00Z'),
  ('obs-fra-air', 'rate', 'FRA–ORD air buy', '{"role":"buy","mode":"air","leg":1,"origin":"DEFRA","destination":"USORD","origin_name":"Frankfurt","destination_name":"Chicago","amount_usd":3840,"rate_display":"4.80 USD/kg","quantity_kg":800,"unit":"per_kg","origin_lon":8.68,"origin_lat":50.11,"destination_lon":-87.63,"destination_lat":41.88}'::jsonb, '[]'::jsonb, '2026-11-03T00:00:00Z', '2026-11-06T00:00:00Z'),
  ('obs-ord-sell', 'rate', 'FRA–ORD air sell', '{"role":"sell","mode":"air","amount_usd":5120,"rate_display":"6.40 USD/kg","quantity_kg":800,"unit":"per_kg"}'::jsonb, '[]'::jsonb, '2026-11-03T00:00:00Z', '2026-11-05T00:00:00Z'),
  ('obs-ord-allotment', 'capacity', 'ORD air allotment', '{"role":"capacity","confirmed":true,"note":"800 kg allotment confirmed"}'::jsonb, '[]'::jsonb, '2026-11-03T00:00:00Z', '2026-11-06T00:00:00Z'),
  ('obs-cavity', 'measurement', 'Cavity temperature samples', '{"role":"series","unit":"degC","samples":[198.2,201.4,199.1,null]}'::jsonb, '["sample_4"]'::jsonb, '2026-10-28T14:00:00Z', '2026-10-28T14:10:00Z'),
  ('obs-dimension', 'measurement', 'Critical dimension', '{"role":"measurement","measured_mm":12.42,"nominal_mm":12.30,"tolerance_mm":0.10,"unit":"mm"}'::jsonb, '[]'::jsonb, '2026-10-28T15:00:00Z', '2026-10-28T15:05:00Z')
on conflict (id) do nothing;

insert into data_stamps (id, observation_id, source_name, content_hash, frame, units, uncertainty, review_status) values
  ('stamp-obs-sha-ocean', 'obs-sha-ocean', 'sample-rate-sheet', 'seed:obs-sha-ocean', 'quote-window', '{"amount":"USD"}'::jsonb, '{"bunker_adjustment":"missing"}'::jsonb, 'synthetic_sample'),
  ('stamp-obs-yvr-rail', 'obs-yvr-rail', 'sample-rate-sheet', 'seed:obs-yvr-rail', 'quote-window', '{"amount":"USD"}'::jsonb, '{"rail_space":"unconfirmed"}'::jsonb, 'synthetic_sample'),
  ('stamp-obs-yyz-dray', 'obs-yyz-dray', 'sample-rate-sheet', 'seed:obs-yyz-dray', 'quote-window', '{"amount":"USD"}'::jsonb, '{}'::jsonb, 'synthetic_sample'),
  ('stamp-obs-sha-sell', 'obs-sha-sell', 'sample-offer', 'seed:obs-sha-sell', 'offer', '{"amount":"USD"}'::jsonb, '{}'::jsonb, 'synthetic_sample'),
  ('stamp-obs-yvr-space', 'obs-yvr-space', 'sample-capacity', 'seed:obs-yvr-space', 'quote-window', '{}'::jsonb, '{"confirmed":false}'::jsonb, 'synthetic_sample'),
  ('stamp-obs-rtm-ocean', 'obs-rtm-ocean', 'sample-rate-sheet', 'seed:obs-rtm-ocean', 'quote-window', '{"amount":"USD"}'::jsonb, '{}'::jsonb, 'synthetic_sample'),
  ('stamp-obs-mtl-road', 'obs-mtl-road', 'sample-rate-sheet', 'seed:obs-mtl-road', 'quote-window', '{"amount":"USD"}'::jsonb, '{}'::jsonb, 'synthetic_sample'),
  ('stamp-obs-rtm-sell', 'obs-rtm-sell', 'sample-offer', 'seed:obs-rtm-sell', 'offer', '{"amount":"USD"}'::jsonb, '{}'::jsonb, 'synthetic_sample'),
  ('stamp-obs-mtl-space', 'obs-mtl-space', 'sample-capacity', 'seed:obs-mtl-space', 'quote-window', '{}'::jsonb, '{"confirmed":true}'::jsonb, 'synthetic_sample'),
  ('stamp-obs-fra-air', 'obs-fra-air', 'sample-rate-sheet', 'seed:obs-fra-air', 'air-window', '{"amount":"USD","quantity":"kg"}'::jsonb, '{}'::jsonb, 'synthetic_sample'),
  ('stamp-obs-ord-sell', 'obs-ord-sell', 'sample-offer', 'seed:obs-ord-sell', 'offer', '{"amount":"USD","quantity":"kg"}'::jsonb, '{}'::jsonb, 'synthetic_sample'),
  ('stamp-obs-ord-allotment', 'obs-ord-allotment', 'sample-capacity', 'seed:obs-ord-allotment', 'air-window', '{}'::jsonb, '{"confirmed":true}'::jsonb, 'synthetic_sample'),
  ('stamp-obs-cavity', 'obs-cavity', 'sample-instrument', 'seed:obs-cavity', 'machine-frame', '{"temperature":"degC"}'::jsonb, '{"sample_4":"missing"}'::jsonb, 'synthetic_sample'),
  ('stamp-obs-dimension', 'obs-dimension', 'sample-instrument', 'seed:obs-dimension', 'part-frame', '{"length":"mm"}'::jsonb, '{}'::jsonb, 'synthetic_sample')
on conflict (id) do nothing;

insert into investigations (id, question, status, context) values
  ('inv-sha-yyz', 'Is the Shanghai to Toronto 40HC margin executable once capacity and bunker are confirmed?', 'open', '{"lane":"SHA-YYZ","operation_id":"freight.margin.v1","allowed_operations":["freight.margin.v1","freight.routes.v1"],"observation_ids":["obs-sha-ocean","obs-yvr-rail","obs-yyz-dray","obs-sha-sell","obs-yvr-space"]}'::jsonb),
  ('inv-rtm-yyz', 'Is the Rotterdam to Toronto 40HC margin executable on the retained window?', 'open', '{"lane":"RTM-YYZ","operation_id":"freight.margin.v1","allowed_operations":["freight.margin.v1","freight.routes.v1"],"observation_ids":["obs-rtm-ocean","obs-mtl-road","obs-rtm-sell","obs-mtl-space"]}'::jsonb),
  ('inv-fra-ord', 'Is the Frankfurt to Chicago 800 kg air margin executable on the retained allotment?', 'open', '{"lane":"FRA-ORD","operation_id":"freight.margin.v1","allowed_operations":["freight.margin.v1","freight.routes.v1"],"observation_ids":["obs-fra-air","obs-ord-sell","obs-ord-allotment"]}'::jsonb),
  ('inv-cavity', 'What do the retained cavity temperature samples support, including the missing point?', 'open', '{"lane":"cavity","operation_id":"analysis.stats.v1","allowed_operations":["analysis.stats.v1"],"observation_ids":["obs-cavity"]}'::jsonb),
  ('inv-dimension', 'Does the retained critical dimension sit inside its declared tolerance?', 'open', '{"lane":"dimension","operation_id":"polymer.tolerance.v1","allowed_operations":["polymer.tolerance.v1"],"observation_ids":["obs-dimension"]}'::jsonb)
on conflict (id) do nothing;

insert into relationships (id, kind, from_id, to_id, note) values
  ('rel-sha-1', 'physical_flow', 'obs-sha-ocean', 'obs-yvr-rail', 'ocean handoff to rail'),
  ('rel-sha-2', 'physical_flow', 'obs-yvr-rail', 'obs-yyz-dray', 'rail handoff to road'),
  ('rel-sha-3', 'lineage', 'obs-sha-sell', 'obs-sha-ocean', 'sell depends on ocean buy'),
  ('rel-sha-4', 'dependency', 'inv-sha-yyz', 'obs-yvr-space', 'capacity still open'),
  ('rel-rtm-1', 'physical_flow', 'obs-rtm-ocean', 'obs-mtl-road', 'ocean handoff to road'),
  ('rel-rtm-2', 'lineage', 'obs-rtm-sell', 'obs-rtm-ocean', 'sell depends on ocean buy'),
  ('rel-air-1', 'dependency', 'inv-fra-ord', 'obs-ord-allotment', 'allotment confirmed'),
  ('rel-cav-1', 'lineage', 'inv-cavity', 'obs-cavity', 'stats read this series'),
  ('rel-dim-1', 'lineage', 'inv-dimension', 'obs-dimension', 'tolerance reads this measurement')
on conflict (id) do nothing;
