"use strict";
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

// Execute exactly the source helper used by EN LAB, not a duplicate translator.
const source = fs.readFileSync("scripts/central-lab-runtime-en.js", "utf8");
const begin = source.indexOf("function fseDiscoveryEnglishCopy(f){");
const end = source.indexOf("\nfunction fseDiscoveryPanel(d){", begin);
assert(begin >= 0 && end > begin, "EN runtime translation helper missing");
const translate = vm.runInNewContext(source.slice(begin, end) + "\nfseDiscoveryEnglishCopy;");
const data = JSON.parse(fs.readFileSync("data/investments/fse_discovery_public.json", "utf8"));
assert(Array.isArray(data.families) && data.families.length > 0);

const polish = /Wielopulsowy|Falowy|Ciągły|wznoszący|opadający|boczny|zwarty|poszarpany|zmienny|Rodzina|hipoteza badawcza|historycznych analogiach|przebieg|kierunku|zastosowania|bez dowodu|[ąćęłńóśźż]/i;
for (const existing of data.families) {
  // Verify live old-format Polish-only projections also render in English.
  const old = {...existing};
  delete old.name_en;
  delete old.description_en;
  delete old.applications_en;
  const translated = translate(old);
  assert(translated.name.startsWith("Multi-pulse") ||
         translated.name.startsWith("Wave-like") ||
         translated.name.startsWith("Continuous") ||
         translated.name.startsWith("Autonomously discovered"), existing.family_id);
  assert(!polish.test(translated.name), "Polish name: "+translated.name);
  assert(!polish.test(translated.description), "Polish description: "+translated.description);
  assert.equal(translated.applications.length, 0);
  for (const application of translated.applications) {
    assert(!polish.test(application), "Polish application: "+application);
  }
  assert.match(translated.description, /not proof of prospective predictive performance/i);
  assert.match(translated.description, new RegExp("\\b"+existing.historical_sample_n+"\\b"));
}
const customized = translate({...data.families[0],
  name_en:"Research-defined family title",
  description_en:"English research conclusion.",
  applications_en:["English candidate use."]});
assert.equal(customized.name,"Research-defined family title");
assert.equal(customized.description,"English research conclusion.");
assert.deepEqual(Array.from(customized.applications),["English candidate use."]);
// Only a prospective REVIEW_CANDIDATE may use this validated application; UI otherwise suppresses it.
assert(source.includes("esc(english.name)") && source.includes("esc(english.description)"));
assert(source.includes("const apps=f.application_status==='RESEARCH_CANDIDATE'"));
assert(source.includes("Application undetermined."));
assert(source.includes("fseDiscoveryShape(f)"));
assert(!source.includes("'<h4>'+esc(f.name)") && !source.includes("'<p>'+esc(f.description)"));

console.log("FSE_ASD_EN_LOCALE_PASS old Polish-only families:",data.families.length);
