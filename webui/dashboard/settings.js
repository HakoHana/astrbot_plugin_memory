/* settings.js — 独立配置页渲染，不依赖 app.js */
(function() {

function esc(v) {
  return String(v).replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function buildHTML(groups) {
  var html = "";
  for (var name in groups) {
    if (name === "模型提供商") continue;
    var fields = groups[name];
    html += '<div class="card"><h2>' + esc(name) + '</h2>';
    for (var i = 0; i < fields.length; i++) {
      var f = fields[i];
      var id = "cfg_" + f.key;
      var val = f.value;
      var hint = f.hint ? '<span class="field-hint">' + esc(f.hint) + '</span>' : "";
      var input = "";
      if (f.type === "bool") {
        input = '<label><input type="checkbox" id="' + id + '" ' + (val ? "checked" : "") + '> ' + esc(f.label) + hint + "</label>";
        html += '<div class="field">' + input + "</div>";
        continue;
      } else if (f.type === "text") {
        input = '<textarea id="' + id + '" rows="3" style="width:260px;padding:7px 10px;border:1px solid #d1d1d6;border-radius:8px;font-size:0.9em">' + esc(val) + "</textarea>";
      } else if (f.type === "select") {
        var opts = "";
        for (var j = 0; j < f.options.length; j++) {
          opts += '<option value="' + f.options[j] + '"' + (val === f.options[j] ? " selected" : "") + ">" + f.options[j] + "</option>";
        }
        input = '<select id="' + id + '" style="width:260px;padding:7px 10px;border:1px solid #d1d1d6;border-radius:8px;font-size:0.9em">' + opts + "</select>";
      } else {
        input = '<input type="text" id="' + id + '" value="' + esc(val) + '" style="width:260px;padding:7px 10px;border:1px solid #d1d1d6;border-radius:8px;font-size:0.9em">';
      }
      html += '<div class="field"><label style="flex:1;font-size:0.92em;font-weight:500;color:#333;min-width:160px">' + esc(f.label) + hint + "</label>" + input + "</div>";
    }
    html += "</div>";
  }
  // 模型提供商
  html += '<div class="card"><h2> 模型提供商</h2>';
  html += '<p style="color:#888;font-size:0.85em;margin-bottom:12px">在「基础」中选用的 ID 需与此处一致</p>';
  html += '<table class="settings-prov-table" style="width:100%;border-collapse:collapse;font-size:0.88em">';
  html += '<thead><tr style="background:#f5f5f7">';
  html += '<th style="padding:8px 6px;text-align:left;font-weight:600">ID</th>';
  html += '<th style="padding:8px 6px;text-align:left;font-weight:600">API 地址</th>';
  html += '<th style="padding:8px 6px;text-align:left;font-weight:600">API Key</th>';
  html += '<th style="padding:8px 6px;text-align:left;font-weight:600">模型</th><th></th>';
  html += '</tr></thead><tbody id="settings-prov-tbody"></tbody></table>';
  html += '<button class="btn-sm" onclick="renderSettingsPage.addProv()" style="margin-top:8px;background:#e8e8ed;border:none;padding:6px 16px;border-radius:16px;cursor:pointer">+ 添加</button>';
  html += '</div>';
  // 系统操作
  html += '<div class="card"><h2> 系统</h2>';
  html += '<button onclick="renderSettingsPage.shutdown()" style="padding:8px 24px;background:#e33;color:#fff;border:none;border-radius:8px;font-size:0.9em;cursor:pointer">⏻ 停止 memori 服务</button>';
  html += '</div>';
  // 保存按钮
  html += '<button onclick="renderSettingsPage.save()" style="margin-top:16px;padding:10px 32px;background:#06c;color:#fff;border:none;border-radius:8px;font-size:1em;cursor:pointer;width:100%;font-weight:500">💾 保存全部</button>';
  return html;
}

function save() {
  var body = {};
  document.querySelectorAll("#page-settings .field").forEach(function(field) {
    var input = field.querySelector("input, select, textarea");
    if (!input) return;
    var key = input.id.replace("cfg_", "");
    if (input.type === "checkbox") body[key] = input.checked;
    else if (input.tagName === "SELECT") body[key] = input.value;
    else body[key] = input.value;
  });
  // 保存提供商
  var providers = [];
  document.querySelectorAll("#settings-prov-tbody tr").forEach(function(tr) {
    var name = tr.querySelector(".pv_n")?.value?.trim();
    if (!name) return;
    providers.push({
      name: name,
      api_base: tr.querySelector(".pv_b")?.value?.trim() || "",
      api_key: tr.querySelector(".pv_k")?.value || "",
      model: tr.querySelector(".pv_m")?.value?.trim() || "",
    });
  });
  Promise.all([
    fetch("/api/v1/config", { method: "PUT", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body) }),
    fetch("/api/v1/providers", { method: "PUT", headers: {"Content-Type":"application/json"}, body: JSON.stringify({providers: providers}) }),
  ]).then(function() {
    var el = document.getElementById("settings-toast");
    if (el) { el.textContent = "✅ 已保存"; el.style.display = "block"; setTimeout(function() { el.style.display = "none"; }, 2000); }
  });
}

function loadProvs() {
  fetch("/api/v1/providers").then(function(r){return r.json()}).then(function(d){
    if (!d.ok) return;
    var tb = document.getElementById("settings-prov-tbody");
    if (!tb) return;
    tb.innerHTML = "";
    for (var i=0; i<(d.providers||[]).length; i++) addProvRow(d.providers[i]);
  });
}

function addProvRow(p) {
  p = p || {};
  var tb = document.getElementById("settings-prov-tbody");
  if (!tb) return;
  var tr = document.createElement("tr");
  tr.innerHTML = '<td><input class="pv_n" value="' + esc(p.name||"") + '" style="width:100%;padding:6px 8px;border:1px solid #ddd;border-radius:6px;box-sizing:border-box"></td>' +
    '<td><input class="pv_b" value="' + esc(p.api_base||"") + '" placeholder="https://api.openai.com/v1" style="width:100%;padding:6px 8px;border:1px solid #ddd;border-radius:6px;box-sizing:border-box"></td>' +
    '<td><input class="pv_k" type="password" value="' + esc(p.api_key||"") + '" style="width:100%;padding:6px 8px;border:1px solid #ddd;border-radius:6px;box-sizing:border-box"></td>' +
    '<td><input class="pv_m" value="' + esc(p.model||"") + '" placeholder="gpt-4o" style="width:100%;padding:6px 8px;border:1px solid #ddd;border-radius:6px;box-sizing:border-box"></td>' +
    '<td><button onclick="this.closest(\'tr\').remove()" style="background:none;border:none;cursor:pointer;color:#999;font-size:18px">✕</button></td>';
  tb.appendChild(tr);
}

window.renderSettingsPage = {
  render: function() {
    var body = document.getElementById("settings-body");
    if (!body) return;
    var cfg = window.__MEMORI_CONFIG__;
    if (!cfg || !cfg.groups) {
      body.innerHTML = '<p style="padding:40px;text-align:center;color:#999">暂无配置数据</p>';
      return;
    }
    body.innerHTML = buildHTML(cfg.groups);
    body.innerHTML += '<div id="settings-toast" style="display:none;position:fixed;bottom:30px;left:50%;transform:translateX(-50%);background:#1d1d1f;color:#fff;padding:10px 24px;border-radius:24px;font-size:14px"></div>';
    loadProvs();
  },
  save: save,
  addProv: function() { addProvRow({}); },
  shutdown: function() {
    if (!confirm("确定停止 memori 服务？")) return;
    fetch("/api/v1/shutdown", { method: "POST" });
  },
};

})();
