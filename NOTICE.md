# NOTICE

3amBench / AlertForge is licensed under the Apache License 2.0 (see LICENSE).

## Third-party material

- **Distractor alert rules** (`src/alertforge/distractors.py`, and the `rules/{node-exporter,kubernetes,blackbox,
  postgres,redis,kafka,nginx,jvm}.yml` files in generated tasks) are written in the style of, and several are
  adapted from, [samber/awesome-prometheus-alerts](https://github.com/samber/awesome-prometheus-alerts)
  (CC BY 4.0) and [kubernetes-monitoring/kubernetes-mixin](https://github.com/kubernetes-monitoring/kubernetes-mixin)
  (Apache-2.0). Changes: rule names kept, expressions simplified, `for`/thresholds adjusted, labels set to
  `{severity, team}`, runbook URLs rewritten to each task's fictional domain.
- **Tools** used by the grader, shipped inside the task image (not in this repository):
  `promtool` from Prometheus v3.5.0 and `amtool` from Alertmanager v0.28.1 (Apache-2.0).
- The multiwindow, multi-burn-rate recipe follows the Google SRE Workbook, chapter 5 "Alerting on SLOs".

All companies, services, teams and incidents in the tasks are fictional.
