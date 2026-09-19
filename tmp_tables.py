import json, re
sql = open('backend/legalakshi_schema_v3_final.sql', encoding='utf-8').read()
for t in ['lkp_scoring_severity', 'lkp_provision_status', 'lkp_requirement_type']:
    m = re.search(r"INSERT INTO %s.*?VALUES\s*(.*?);" % t, sql, re.S)
    print(t, '->', re.sub(r'\s+', ' ', m.group(1)[:400]) if m else 'NOT FOUND')
m = json.load(open('backend/authoritative/legalakshi_master_v4.json', encoding='utf-8'))
r = m['legal_knowledge']['rules'][0]
print('rule keys:', sorted(r.keys()))
print(json.dumps(r, indent=1)[:900])
v = m['legal_knowledge']['rule_versions'][0]
print('version keys:', sorted(v.keys()))
a = m['legal_knowledge']['applicability_rules'][0]
print('appl keys:', sorted(a.keys()))
print('scoring:', json.dumps(m['scoring_policy'], indent=1)[:700])
