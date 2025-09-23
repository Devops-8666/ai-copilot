# app.py - simple Reliability Copilot PoC
import os, json, requests
from flask import Flask, jsonify, request, abort
import pandas as pd
from sklearn.ensemble import IsolationForest

try:
    import openai
except:
    openai = None

app = Flask(__name__)

# CONFIG (edit if needed)
PROM_URL = os.getenv("PROM_URL", "")         # set if you have Prometheus
PROM_QUERY = os.getenv("PROM_QUERY", "")     # optional prom query
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "") # optional
ANOMALY_CONTAMINATION = float(os.getenv("ANOMALY_CONTAMINATION", "0.05"))
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK", "")

if OPENAI_KEY and openai:
    openai.api_key = OPENAI_KEY

def load_local_logs(path="logs.json"):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)

def assemble_dataframe(prom_data=None, logs=None):
    rows = []
    if logs:
        for r in logs:
            rows.append(r)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    numcols = df.select_dtypes(include=["int64","float64","float"]).columns.tolist()
    if "value" in df.columns and "value" not in numcols:
        numcols.append("value")
    return df, numcols

def detect_anomalies(df, numcols):
    if df.empty or len(numcols) == 0:
        return pd.DataFrame()
    model = IsolationForest(contamination=ANOMALY_CONTAMINATION, random_state=42)
    X = df[numcols].fillna(0)
    df["_anomaly_flag"] = model.fit_predict(X)
    return df[df["_anomaly_flag"] == -1].copy()

def generate_rca_text(context_str):
    # Use OpenAI if available; otherwise simple fallback
    if OPENAI_KEY and openai:
        try:
            resp = openai.ChatCompletion.create(
                model="gpt-4o-mini",
                messages=[
                    {"role":"system","content":"You are an SRE assistant. Give a one-sentence root cause and one suggested debug command."},
                    {"role":"user","content":context_str}
                ],
                max_tokens=80, temperature=0.2
            )
            return resp.choices[0].message["content"].strip()
        except Exception as e:
            return f"RCA fallback: inspect logs/metrics. (openai error)"
    # simple heuristic
    low = context_str.lower()
    if "error" in low or "5xx" in low:
        return "High error rate — check app logs and recent deploys. Suggested: kubectl logs <pod> -n <ns>"
    if "oom" in low or "oomkilled" in low:
        return "OOMKilled — likely memory limit; inspect pod and deployment requests/limits."
    if "latency" in low or "ms" in low:
        return "Latency spike — check backend downstreams and CPU/threads."
    return "Anomaly detected — check pod logs and metrics (kubectl describe/logs)."

def make_debug_commands(row):
    pod = row.get("pod", "<pod>")
    ns = row.get("namespace", "default")
    return {
        "describe": f"kubectl describe pod {pod} -n {ns}",
        "logs": f"kubectl logs {pod} -n {ns} --tail=200",
        "top": f"kubectl top pod {pod} -n {ns}"
    }

@app.route("/anomalies", methods=["GET"])
def anomalies():
    logs = load_local_logs()
    df_num = pd.DataFrame()
    df, numcols = assemble_dataframe(None, logs)
    anomalies_df = detect_anomalies(df, numcols) if not df.empty else pd.DataFrame()
    results = []
    for _, r in anomalies_df.iterrows():
        context = json.dumps(r.to_dict(), default=str)
        rca = generate_rca_text(context)
        cmds = make_debug_commands(r)
        results.append({"row": r.to_dict(), "rca": rca, "commands": cmds})
    return jsonify({"count": len(results), "items": results})

@app.route("/scan-and-notify", methods=["POST"])
def scan_and_notify():
    resp = anomalies().get_json()
    if resp["count"] == 0:
        return jsonify({"notified": False, "reason": "no anomalies"})
    summary = f"Reliability Copilot: {resp['count']} anomaly(ies). First RCA: {resp['items'][0]['rca']}"
    if SLACK_WEBHOOK:
        try:
            requests.post(SLACK_WEBHOOK, json={"text": summary}, timeout=5)
        except:
            pass
    return jsonify({"notified": True, "summary": summary})

# Safe action endpoint for demo (requires approval token)
APPROVAL_TOKEN = os.getenv("APPROVAL_TOKEN", "demo-approve-token")
@app.route("/run-action", methods=["POST"])
def run_action():
    token = request.headers.get("X-APPROVAL-TOKEN","")
    if token != APPROVAL_TOKEN:
        abort(403, "approval token missing/invalid")
    action = request.json.get("action")
    # PoC: echo back the action. DO NOT run shell commands in production automatically.
    return jsonify({"status":"ok","executed":action})

@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

