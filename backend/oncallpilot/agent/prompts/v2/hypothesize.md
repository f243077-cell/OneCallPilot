Based only on the evidence so far, give 1 to 3 hypotheses about the root cause, most likely first.

For each hypothesis:
- `rank`: 1 for the most likely;
- `summary`: one or two sentences naming the cause and its mechanism;
- `root_cause_category`: the best-fitting category;
- `confidence`: your probability from 0 to 1 that it is the root cause;
- `evidence_refs`: the references (E1, E2, …) that support it. Cite only references you were given; a hypothesis citing an unknown reference is discarded.

Do not invent facts that no evidence shows. If two causes are equally plausible, say so through their confidences.

Code checks every citation against the hypothesis's category. Each evidence item shows its source (`source="…"`); detector and runbooks are accepted for every category. A citation from any other source the category does not accept is removed, and a hypothesis that loses a citation, or that cites no source its category requires, has its confidence halved. So cite the references that support the hypothesis, only from sources its category accepts, including at least one it requires.

Sources by category:

- `bad_deploy`: requires deploy_ledger or prometheus; also accepts loki, docker
- `config_error`: requires deploy_ledger or loki; also accepts docker, prometheus
- `memory_leak`: requires prometheus or docker; also accepts loki, deploy_ledger
- `resource_saturation`: requires prometheus or docker; also accepts loki, deploy_ledger
- `traffic_surge`: requires prometheus; also accepts loki, docker
- `dependency_unavailable`: requires loki or docker; also accepts prometheus
- `dependency_slow`: requires prometheus; also accepts loki, docker
- `db_connection_exhaustion`: requires prometheus or loki; also accepts docker
- `cache_failure`: requires prometheus or loki; also accepts docker, deploy_ledger
- `application_bug`: requires loki; also accepts deploy_ledger, prometheus, docker
- `network_issue`: requires loki or prometheus or docker
- `disk_pressure`: requires docker or loki; also accepts prometheus
- `unknown`: requires none; also accepts loki, prometheus, deploy_ledger, docker
