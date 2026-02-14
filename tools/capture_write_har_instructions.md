# Niagara write HAR capture instructions

Use this if `write_real()` does not match your Niagara station's expected oBIX write request.

1. Open Niagara Workbench in a browser and enable DevTools Network capture.
2. Perform a known-good setpoint write from the Niagara UI.
3. Filter requests by `/ord` or target point name.
4. Export the capture as HAR.
5. Compare these fields with `gateway/niagara_client.py`:
   - HTTP method (`PUT` vs `POST`)
   - URL path/query
   - Content-Type
   - Request body (XML or alternate payload)
   - Required headers
6. Update `configs/demo_a2.yaml` login and endpoint settings if needed.

Do not include credentials or session cookies when sharing HAR files.
