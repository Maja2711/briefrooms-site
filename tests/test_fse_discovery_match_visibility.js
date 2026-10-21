import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

for (const [path,header] of [
  ["scripts/central-lab.js", "Aktualne dopasowanie:"],
  ["scripts/central-lab-runtime-en.js", "Current match:"],
]) {
  const source = fs.readFileSync(path, "utf8");
  const start = source.indexOf("function fseDiscoveryPanel(d){");
  const end = source.indexOf("\nasync function fse(){", start);
  assert(start >= 0 && end > start, "FSE renderer not found: " + path);
  const render = vm.runInNewContext(source.slice(start, end) + "\nfseDiscoveryPanel;", {
    Date,
    Set,
    Number,
    Array,
    String,
    esc: value => String(value ?? ""),
    num: value => String(value ?? "0"),
    finite: value => typeof value === "number" && Number.isFinite(value),
    fseInstrumentLabel: value => String(value),
    fseDiscoveryShape: () => "",
    fseDiscoveryEnglishCopy: family => ({
      name: family.name_en || "Test family",
      description: family.description_en || "Test description",
      applications: [],
    }),
  });
  const known = {
    family_id: "ASD-EXAMPLE", instrument: "BTCUSD", name: "Test family",
    name_en: "Test family", description: "Test description",
    description_en: "Test description", historical_sample_n: 40,
    prospective_n: 0, applications: [], application_status: "UNDETERMINED",
    evidence_status: "HISTORICAL_DISCOVERY_ONLY", window: 32,
  };
  const base = {
    schema_version: "FSE-ASD-1.0",
    generated_at: new Date().toISOString(),
    summary: {families: 1, frozen_snapshots: 3, prospective_resolutions: 0},
    families: [known],
    current_matches: [],
  };
  const unknown = {
    instrument: "BTCUSD", status: "FROZEN_NOVEL_UNMATCHED",
    family_id: null,
  };
  const active = {
    instrument: "BTCUSD", status: "FROZEN_MATCH",
    family_id: "ASD-EXAMPLE",
  };
  const staleRetired = {
    instrument: "EURUSD", status: "RETIRED_NOT_MATCHED",
    family_id: "ASD-RETIRED",
  };

  const onlyUnknown = render({...base, current_matches: [unknown]});
  assert(!onlyUnknown.includes(header), "Unexpected banner for unknown shape: "+path);
  assert(!onlyUnknown.includes("New/unmatched shape"), "Unexpected unknown label: "+path);
  assert(!onlyUnknown.includes("Nowa / niedopasowana struktura"), "Unexpected unknown label: "+path);
  assert(onlyUnknown.includes("ASD-EXAMPLE"), "Catalog family must remain visible: "+path);

  const mixed = render({...base, current_matches: [unknown, active, staleRetired]});
  assert(mixed.includes(header), "Verified active family match must stay visible: "+path);
  assert(mixed.includes("ASD-EXAMPLE"), "Active match missing: "+path);
  assert(!mixed.includes("ASD-RETIRED"), "Retired match must not appear: "+path);

  const retiredOnly = render({...base, current_matches: [
    {instrument: "BTCUSD", status: "FROZEN_MATCH", family_id: "ASD-RETIRED"},
  ]});
  assert(!retiredOnly.includes(header), "Family absent from active catalogue must not be displayed: "+path);

  const many=Array.from({length:20},(_,index)=>({...known,family_id:"ASD-CATALOGUE-"+index}));
  const catalogue=render({...base,families:many});
  assert(catalogue.includes("ASD-CATALOGUE-19"), "No valid family may disappear after a 12-card cut: "+path);
  assert(catalogue.includes("fse-discovery-catalogue"), "Large catalogue must remain expandable: "+path);
  const old=render({...base,generated_at:"2020-01-01T00:00:00Z",current_matches:[active]});
  assert(!old.includes(header), "Stale catalogue must not claim a current match: "+path);
  console.log("FSE_MATCH_VISIBILITY_PASS", path);
}
