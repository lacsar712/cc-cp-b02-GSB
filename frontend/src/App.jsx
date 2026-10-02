import { useCallback, useEffect, useState } from "preact/hooks";

const TOKEN_KEY = "coldchain_token";
const USER_KEY = "coldchain_user";

function verdictClass(v, status) {
  if (v === "合格") return "tag pass";
  if (v === "超温") return "tag fail";
  if (status === "pending" || status === "processing") return "tag wait";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

function fmtTime(iso) {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(0, 19);
}

export function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch {
      return null;
    }
  });
  const [view, setView] = useState("readings");
  const [loginForm, setLoginForm] = useState({ username: "logger", password: "log123456" });
  const [submitForm, setSubmitForm] = useState({ probe_id: "", temp_c: "" });
  const [rows, setRows] = useState([]);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const authHeaders = useCallback(() => {
    const h = { "Content-Type": "application/json" };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  const loadReadings = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/readings", { headers: authHeaders() });
    if (!res.ok) {
      setError("加载列表失败，请重新登录");
      return;
    }
    setRows(await res.json());
  }, [token, authHeaders]);

  useEffect(() => {
    if (view !== "readings" || !token) return undefined;
    loadReadings();
    const t = setInterval(loadReadings, 3000);
    return () => clearInterval(t);
  }, [loadReadings, token, view]);

  async function onLogin(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(loginForm),
      });
      if (!res.ok) {
        setError("用户名或密码错误");
        return;
      }
      const data = await res.json();
      localStorage.setItem(TOKEN_KEY, data.access_token);
      localStorage.setItem(
        USER_KEY,
        JSON.stringify({ username: data.username, role: data.role })
      );
      setToken(data.access_token);
      setUser({ username: data.username, role: data.role });
    } finally {
      setLoading(false);
    }
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
    setRows([]);
    setView("readings");
  }

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    setLoading(true);
    try {
      const res = await fetch("/api/readings", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          probe_id: submitForm.probe_id,
          temp_c: parseFloat(submitForm.temp_c),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "提交失败");
        return;
      }
      setMsg(data.message || "已提交");
      setSubmitForm({ probe_id: "", temp_c: "" });
      await loadReadings();
    } finally {
      setLoading(false);
    }
  }

  if (!token) {
    return (
      <div class="wrap">
        <h1>冷链探头超温台</h1>
        <p class="sub">记录员提交探头编号与摄氏温度，后台工人认领后判定合格或超温。</p>
        <div class="card">
          <form onSubmit={onLogin}>
            <div class="row">
              <label>
                用户名
                <input
                  value={loginForm.username}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, username: e.target.value })
                  }
                />
              </label>
              <label>
                密码
                <input
                  type="password"
                  value={loginForm.password}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, password: e.target.value })
                  }
                />
              </label>
              <button type="submit" disabled={loading}>
                登录
              </button>
            </div>
            {error && <p class="err">{error}</p>}
          </form>
          <p class="sub" style={{ marginBottom: 0 }}>
            记录员 logger / log123456 · 值班员 watcher / watch123456
          </p>
        </div>
      </div>
    );
  }

  const isWriter = user?.role === "writer";

  return (
    <div class="wrap">
      <div class="topbar">
        <div>
          <h1>冷链探头超温台</h1>
          <p class="sub">温度不超过 8℃ 为合格，否则为超温。</p>
        </div>
        <div class="user">
          <nav class="tabs">
            <button
              type="button"
              class={view === "readings" ? "tab active" : "tab"}
              onClick={() => setView("readings")}
            >
              读数台
            </button>
            <button
              type="button"
              class={view === "reminder" ? "tab active" : "tab"}
              onClick={() => setView("reminder")}
            >
              催领
            </button>
          </nav>
          {user?.username}（{isWriter ? "记录员" : "值班员"}）
          <button type="button" class="secondary" style={{ marginLeft: "0.5rem" }} onClick={logout}>
            退出
          </button>
        </div>
      </div>

      {view === "readings" && (
        <ReadingsView
          isWriter={isWriter}
          rows={rows}
          submitForm={submitForm}
          setSubmitForm={setSubmitForm}
          onSubmit={onSubmit}
          loading={loading}
          error={error}
          msg={msg}
        />
      )}
      {view === "reminder" && <ReminderView isWriter={isWriter} authHeaders={authHeaders} />}
    </div>
  );
}

function ReadingsView({ isWriter, rows, submitForm, setSubmitForm, onSubmit, loading, error, msg }) {
  return (
    <>
      {isWriter && (
        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>提交读数</h2>
          <form onSubmit={onSubmit}>
            <div class="row">
              <label>
                探头编号
                <input
                  required
                  value={submitForm.probe_id}
                  onInput={(e) =>
                    setSubmitForm({ ...submitForm, probe_id: e.target.value })
                  }
                  placeholder="例如 探头C03"
                />
              </label>
              <label>
                温度（℃）
                <input
                  required
                  type="number"
                  step="0.1"
                  value={submitForm.temp_c}
                  onInput={(e) =>
                    setSubmitForm({ ...submitForm, temp_c: e.target.value })
                  }
                />
              </label>
              <button type="submit" disabled={loading}>
                提交
              </button>
            </div>
            {error && <p class="err">{error}</p>}
            {msg && <p class="ok">{msg}</p>}
          </form>
        </div>
      )}

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>读数列表</h2>
        <table>
          <thead>
            <tr>
              <th>编号</th>
              <th>探头</th>
              <th>温度℃</th>
              <th>结论</th>
              <th>说明</th>
              <th>状态</th>
              <th>提交人</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>{r.probe_id}</td>
                <td>{r.temp_c}</td>
                <td>
                  <span class={verdictClass(r.verdict, r.status)}>
                    {displayVerdict(r)}
                  </span>
                </td>
                <td>{r.reason || "—"}</td>
                <td>{r.status}</td>
                <td>{r.created_by}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colspan="7">暂无数据</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ReminderView({ isWriter, authHeaders }) {
  const [config, setConfig] = useState(null);
  const [entries, setEntries] = useState([]);
  const [logs, setLogs] = useState([]);
  const [draft, setDraft] = useState({ enabled: false, timeout_minutes: 30 });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const loadAll = useCallback(async () => {
    const [cRes, eRes, lRes] = await Promise.all([
      fetch("/api/reminder/config", { headers: authHeaders() }),
      fetch("/api/reminder/entries", { headers: authHeaders() }),
      fetch("/api/reminder/logs", { headers: authHeaders() }),
    ]);
    if (!cRes.ok || !eRes.ok || !lRes.ok) {
      setError("加载催领数据失败，请重新登录");
      return;
    }
    const [cfg, ents, lgs] = await Promise.all([cRes.json(), eRes.json(), lRes.json()]);
    setConfig(cfg);
    // 仅在未保存改动时用服务端值回填表单，避免覆盖正在编辑的分钟数
    setDraft((d) =>
      saving || d.__dirty ? d : { enabled: cfg.enabled, timeout_minutes: cfg.timeout_minutes }
    );
    setEntries(ents);
    setLogs(lgs);
  }, [authHeaders, saving]);

  useEffect(() => {
    loadAll();
    const t = setInterval(loadAll, 3000);
    return () => clearInterval(t);
  }, [loadAll]);

  async function saveConfig(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    const minutes = Number(draft.timeout_minutes);
    if (!Number.isInteger(minutes) || minutes < 1) {
      setError("超时分钟数必须是不小于 1 的整数");
      return;
    }
    setSaving(true);
    try {
      const res = await fetch("/api/reminder/config", {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify({ enabled: draft.enabled, timeout_minutes: minutes }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "保存失败");
        return;
      }
      setDraft({ enabled: data.enabled, timeout_minutes: data.timeout_minutes, __dirty: false });
      setMsg(
        data.enabled
          ? `已开启催领，候审超过 ${data.timeout_minutes} 分钟的读数将由服务端写入清单`
          : "已关闭催领，立即停止新催领；已有清单行保留"
      );
      await loadAll();
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>催领设置</h2>
        <p class="sub" style={{ marginBottom: "0.75rem" }}>
          候审（待处理）读数超过阈值后由服务端按数据库时钟对账入催领清单，页面不做前端倒计时。
        </p>
        {isWriter ? (
          <form onSubmit={saveConfig}>
            <div class="row">
              <label>
                超时阈值（分钟）
                <input
                  type="number"
                  min="1"
                  step="1"
                  value={draft.timeout_minutes}
                  onInput={(e) =>
                    setDraft({ ...draft, timeout_minutes: e.target.value, __dirty: true })
                  }
                />
              </label>
              <label class="switch-label">
                催领开关
                <button
                  type="button"
                  role="switch"
                  aria-checked={draft.enabled ? "true" : "false"}
                  class={draft.enabled ? "switch on" : "switch"}
                  onClick={() => setDraft({ ...draft, enabled: !draft.enabled, __dirty: true })}
                >
                  <span class="knob" />
                </button>
              </label>
              <button type="submit" disabled={saving}>
                保存设置
              </button>
            </div>
            {error && <p class="err">{error}</p>}
            {msg && <p class="ok">{msg}</p>}
            {config && (
              <p class="sub" style={{ marginBottom: 0, marginTop: "0.5rem", fontSize: "0.8rem" }}>
                当前已生效：{config.enabled ? "开启" : "关闭"} · 阈值 {config.timeout_minutes} 分钟
                {config.updated_by ? ` · 最后修改 ${config.updated_by} ${fmtTime(config.updated_at)}` : ""}
              </p>
            )}
          </form>
        ) : (
          <div>
            <p class="readonly-box">
              当前催领状态：<strong>{config?.enabled ? "开启" : "关闭"}</strong>
              {config ? ` · 超时阈值 ${config.timeout_minutes} 分钟` : ""}
            </p>
            <p class="sub" style={{ marginBottom: 0, fontSize: "0.85rem" }}>
              值班员只读催领清单与流水，不可修改阈值与开关。
            </p>
          </div>
        )}
      </div>

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>
          催领清单 <span class="count">{entries.length}</span>
        </h2>
        <table>
          <thead>
            <tr>
              <th>读数编号</th>
              <th>探头</th>
              <th>温度℃</th>
              <th>当前状态</th>
              <th>提交人</th>
              <th>候审提交时间</th>
              <th>超时时刻</th>
              <th>入清单时间</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((r) => (
              <tr key={r.reading_id}>
                <td>{r.reading_id}</td>
                <td>{r.probe_id}</td>
                <td>{r.temp_c}</td>
                <td>
                  <span class={verdictClass(null, r.status)}>{displayVerdict(r)}</span>
                </td>
                <td>{r.created_by}</td>
                <td>{fmtTime(r.created_at)}</td>
                <td>{fmtTime(r.timed_out_at)}</td>
                <td>{fmtTime(r.added_at)}</td>
              </tr>
            ))}
            {entries.length === 0 && (
              <tr>
                <td colspan="8">暂无催领记录</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>
          催领流水 <span class="count">{logs.length}</span>
        </h2>
        <table>
          <thead>
            <tr>
              <th>流水号</th>
              <th>读数编号</th>
              <th>探头</th>
              <th>阈值（分钟）</th>
              <th>超时时刻</th>
              <th>记账时间</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>{r.reading_id}</td>
                <td>{r.probe_id}</td>
                <td>{r.timeout_minutes}</td>
                <td>{fmtTime(r.timed_out_at)}</td>
                <td>{fmtTime(r.added_at)}</td>
              </tr>
            ))}
            {logs.length === 0 && (
              <tr>
                <td colspan="6">暂无流水</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
