# Aegis integration and CI verification

This additive update preserves the evaluated `client.bal` and model code. The ten black-box cases exercise the existing native demo with deterministic HTTP and inference stubs, not real trained models. Ten successful warm-up operations fill the history; operation 11 fails once, then returns 200 if retried. Assertions cover client-reported attempts, actual server requests, decision calls, response status and observed deadline flags. The 404 case verifies no recovery. A delayed inference response tests the configured sidecar timeout. Ports 8080/8091 must be unused; stop running local services first. Run from the repository root:

```bash
(cd aegis_native && bal test && bal pack && bal push --repository local)
(cd native_demo && bal build --offline)
python3 tests/integration_native.py
```

GitHub Actions installs Ballerina 2201.10.2 and Python 3.11, compiles the Python sources, runs legacy policy and native unit tests, builds both native packages, and runs the integration suite. No model binaries or secrets are required. Upload the hidden `.github/workflows/aegis-ci.yml` file explicitly if the browser omits hidden folders. CI is verification only, not deployment. A green run does not establish hard deadlines, POST safety, concurrent correctness, model performance, or network impairment robustness. Integration execution and CI have not been validated by the authoring environment because Ballerina is unavailable there.
