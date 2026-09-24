/**
 * 自动采集 · 采集总览子页
 * 状态总览 + 各子页关键配置只读快照 + 快速跳转。实际编辑在对应子页完成。
 */
'use strict';

async function renderAcOverview() {
  acShell('ac-overview', '自动采集运行状态一览 · 各采集通道/类型/内容配置入口 · 点标题进入子页编辑');
  const body = $('#ac-page-body');
  try {
    await acLoadConfig();
  } catch (e) { body.innerHTML = `<p class="err-tip">加载失败：${esc(e.message)}</p>`; return; }
  const cfg = _acCfg || {};
  let st = {};
  try { st = await admin('/admin/autocollect/status') || {}; } catch (e) {}
  let chInfo = [];
  try { const r = await admin('/admin/autocollect/llm/channels'); chInfo = r.channels || []; } catch (e) {}
  const go = (id, label) => `<button class="btn ghost sm" onclick="acSubTab('${id}')">${label}</button>`;
  const line = (k, v) => `<div class="detail-row"><div class="k">${esc(k)}</div><div class="v">${v == null || v === '' ? '<span class="hint">未配置</span>' : v}</div></div>`;
  const listTxt = (arr) => (arr && arr.length) ? arr.map(x => esc(x)).join('、') : '';
  body.innerHTML = `
  ${acControlPanelHtml()}

  <div class="chart-grid" style="margin-top:14px">
    <div class="panel" style="margin:0">
      <div class="panel-head"><h3>🔌 采集类型</h3>${go('ac-types', '编辑 ›')}</div>
      <div class="panel-body">
        ${line('公开题库网址', (cfg.urls || []).length + ' 个')}
        ${line('整站上限 / 逐站额度', `${cfg.max_site_pages || '—'} 页 · ${(Object.keys(cfg.site_pages_map || {}).length)} 站定制`)}
        ${line('联网搜索词', (cfg.search_queries || []).length + ' 个')}
        ${line('自主找题', cfg.autonomous ? '开启' : '关闭')}
        ${line('站点白名单 / 详情页规则', `${(cfg.site_whitelist || []).length} 域 · ${_acSiteRules.length} 条规则`)}
      </div>
    </div>
    <div class="panel" style="margin:0">
      <div class="panel-head"><h3>🛰️ 多通道</h3>${go('ac-channels', '编辑 ›')}</div>
      <div class="panel-body">
        ${line('采集模型通道', chInfo.length ? chInfo.map((c, i) => `${esc(c.model)}${i === 0 ? '(主)' : ''}`).join(' · ') : '回落平台默认模型')}
        ${line('并发线程', cfg.threads + ' 线程' + (cfg.threads > 1 && chInfo.length ? '（并行提取已启用）' : '（串行）'))}
        ${line('备用模型', listTxt(cfg.fallback_models) || '仅主模型')}
        ${line('LLM 预算 / 价格', `${cfg.llm_budget_chars ? (cfg.llm_budget_chars / 1000).toFixed(0) + 'k 字/轮' : '不限'} · ${cfg.llm_price_per_1m || 0} 元/百万token`)}
      </div>
    </div>
    <div class="panel" style="margin:0">
      <div class="panel-head"><h3>📦 采集内容</h3>${go('ac-content', '编辑 ›')}</div>
      <div class="panel-body">
        ${line('每轮入库预算', (cfg.max_per_cycle || '—') + ' 题')}
        ${line('AI 自评抽样率', cfg.quality_sample_rate != null ? cfg.quality_sample_rate : '—')}
        ${line('采集开关 / 周期', `${cfg.enabled ? '启用' : '停用'} · ${cfg.interval_secs ? Math.round(cfg.interval_secs / 60) : '—'} 分钟`)}
        ${line('时间窗口', cfg.schedule_enabled ? `${String(cfg.schedule_start).padStart(2, '0')}:00 → ${String(cfg.schedule_end).padStart(2, '0')}:00` : '不限时段')}
        ${line('Webhook 通知', cfg.webhook_url ? esc(cfg.webhook_url) : '未配置（暂停外发）')}
      </div>
    </div>
    <div class="panel" style="margin:0">
      <div class="panel-head"><h3>📜 采集日志</h3>${go('ac-logs', '查看 ›')}</div>
      <div class="panel-body">
        ${line('最近运行', fmtTime(st.last_run))}
        ${line('运行状态', st.running ? '🟦 运行中' : '空闲')}
        ${line('本轮新增', ((st.last_result || {}).added || 0) + ' 题')}
        ${line('失败来源', ((st.failed_sources || {}).count || 0))}
        ${line('套卷标记', go('ac-content', '查看套卷标记 ›'))}
      </div>
    </div>
  </div>`;
  acRefreshControls();
}
