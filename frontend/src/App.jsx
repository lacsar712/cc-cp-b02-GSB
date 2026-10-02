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

const ACTION_LABELS = {
  switch_on: "打开开关",
  switch_off: "关闭开关",
  threshold_changed: "修改阈值",
  reminded: "写入催领",
};

function actionLabel(action) {
  return ACTION_LABELS[action] || action;
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(
    d.getHours()
  )}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function RemindersPage({ token, user, authHeaders, setError }) {
  const isWriter = user?.role === "writer";
  const [data, setData] = useState(null);
  // 编辑中的分钟数与开关，仅记录员可操作；初始值取自服务端返回
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");

  const load = useCallback(async () => {
    const res = await fetch("/api/reminders", { headers: authHeaders() });
    if (!res.ok) {
      setError("加载催领数据失败，请重新登录");
      return;
    }
    const d = await res.json();
    setData(d);
    setDraft((prev) =>
      prev
        ? prev
        : {
            enabled: d.settings.enabled,
            timeout_minutes: String(d.settings.timeout_minutes),
          }
    );
  }, [authHeaders, setError]);

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load]);

  async function saveSettings(e) {
    e.preventDefault();
    setError("");
    setSaveMsg("");
    const minutes = Number(draft.timeout_minutes);
    if (!Number.isInteger(minutes) || minutes < 1) {
      setError("超时分钟数必须是不小于 1 的整数");
      return;
    }
    setSaving(true);
    try {
      const res = await fetch("/api/reminders/settings", {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify({ enabled: draft.enabled, timeout_minutes: minutes }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(body.detail || "保存失败");
        return;
      }
      setSaveMsg("已保存");
      await load();
    } finally {
      setSaving(false);
    }
  }

  if (!data || !draft) return <div class="card">加载中…</div>;
  const s = data.settings;

  return (
    <div>
      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>催领设置</h2>
        {isWriter ? (
          <form onSubmit={saveSettings}>
            <div class="row">
              <label>
                超时分钟阈值
                <input
                  type="number"
                  min="1"
                  step="1"
                  value={draft.timeout_minutes}
                  onInput={(e) =>
                    setDraft({ ...draft, timeout_minutes: e.target.value })
                  }
                />
              </label>
              <label class="switch-label">
                催领开关
                <input
                  type="checkbox"
                  class="switch"
                  checked={draft.enabled}
                  onChange={(e) =>
                    setDraft({ ...draft, enabled: e.target.checked })
                  }
                />
              </label>
              <button type="submit" disabled={saving}>
                保存
              </button>
              <span class={draft.enabled ? "tag pass" : "tag wait"}>
                {draft.enabled ? "已打开" : "已关闭"}
              </span>
            </div>
            <p class="sub" style={{ marginBottom: 0 }}>
              打开后，服务端按数据库时间把候审超过该分钟数的读数写入催领清单并记流水；
              关闭后立即停止新催领，已有催领行保留。
            </p>
            {saveMsg && <p class="ok">{saveMsg}</p>}
          </form>
        ) : (
          <div class="readonly-box">
            <div>
              当前状态：
              <span class={s.enabled ? "tag pass" : "tag wait"}>
                {s.enabled ? "已打开" : "已关闭"}
              </span>
            </div>
            <div>
              超时分钟阈值：<strong>{s.timeout_minutes}</strong> 分钟
            </div>
            <div class="sub" style={{ marginBottom: 0 }}>
              值班员只读，阈值与开关由记录员维护
              {s.updated_by ? `（最后修改：${s.updated_by} ${fmtTime(s.updated_at)}）` : ""}
            </div>
          </div>
        )}
      </div>

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>
          催领清单（{data.items.length}）
        </h2>
        <table>
          <thead>
            <tr>
              <th>清单编号</th>
              <th>候审单号</th>
              <th>探头</th>
              <th>已等候（分钟）</th>
              <th>读数当前状态</th>
              <th>催领时间</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((it) => (
              <tr key={it.id}>
                <td>{it.id}</td>
                <td>{it.reading_id}</td>
                <td>{it.probe_id}</td>
                <td>{it.waited_minutes}</td>
                <td>
                  <span class="tag wait">{it.reading_status}</span>
                </td>
                <td>{fmtTime(it.reminded_at)}</td>
              </tr>
            ))}
            {data.items.length === 0 && (
              <tr>
                <td colspan="6">暂无催领记录</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>催领流水</h2>
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>操作</th>
              <th>说明</th>
              <th>操作人</th>
            </tr>
          </thead>
          <tbody>
            {data.logs.map((lg) => (
              <tr key={lg.id}>
                <td>{fmtTime(lg.created_at)}</td>
                <td>
                  <span
                    class={
                      lg.action === "reminded"
                        ? "tag fail"
                        : lg.action === "switch_off"
                        ? "tag wait"
                        : "tag pass"
                    }
                  >
                    {actionLabel(lg.action)}
                  </span>
                </td>
                <td>{lg.detail}</td>
                <td>{lg.operator}</td>
              </tr>
            ))}
            {data.logs.length === 0 && (
              <tr>
                <td colspan="4">暂无流水</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
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
  const [view, setView] = useState("home");
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
    loadReadings();
    if (!token) return undefined;
    const t = setInterval(loadReadings, 3000);
    return () => clearInterval(t);
  }, [loadReadings, token]);

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
    setView("home");
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
          <nav class="nav">
            <button
              type="button"
              class={view === "home" ? "navbtn active" : "navbtn"}
              onClick={() => setView("home")}
            >
              读数台
            </button>
            <button
              type="button"
              class={view === "reminders" ? "navbtn active" : "navbtn"}
              onClick={() => setView("reminders")}
            >
              催领
            </button>
          </nav>
        </div>
        <div class="user">
          {user?.username}（{isWriter ? "记录员" : "值班员"}）
          <button type="button" class="secondary" style={{ marginLeft: "0.5rem" }} onClick={logout}>
            退出
          </button>
        </div>
      </div>

      {error && <p class="err">{error}</p>}

      {view === "reminders" ? (
        <RemindersPage
          token={token}
          user={user}
          authHeaders={authHeaders}
          setError={setError}
        />
      ) : (
        <div>
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
        </div>
      )}
    </div>
  );
}
