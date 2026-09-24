/**
 * 自动采集 · 采集类型配置子页
 * 网址采集 / 文档采集 / 搜索采集 三类来源的配置。
 */
'use strict';

async function renderAcTypes() {
  acShell('ac-types', '按采集方式配置来源：公开题库网址整站爬取 · 文档库轮询提取 · 联网搜索自动找题');
  const body = $('#ac-page-body');
  try {
    await acLoadConfig();
  } catch (e) { body.innerHTML = `<p class="err-tip">加载失败：${esc(e.message)}</p>`; return; }
  const cfg = _acCfg || {};
  body.innerHTML = `
  ${acControlPanelHtml()}
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap"><b>采集类型配置</b>
        <span class="hint" style="margin-left:8px">保存即热生效（无需重建容器）；三类来源可并存，采集时按「来源优先级」依次处理</span></div>
      <button class="btn primary sm" onclick="acSaveConfig()">💾 保存配置</button>
    </div>
    <div style="padding:14px">

      <!-- 网址采集 -->
      <div style="border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:14px">
        <div style="font-weight:700;color:var(--wood-deep);margin-bottom:10px">🌐 网址采集（公开题库整站爬取 → LLM 提取）</div>
        <div style="display:flex;flex-wrap:wrap;gap:14px">
          <div class="field" style="flex:2;min-width:320px"><label>公开题库网址（每行一个；已处理网址每轮按内容变化增量重采）</label><textarea id="ac-urls" rows="4" placeholder="https://…">${esc((cfg.urls || []).join('\n'))}</textarea></div>
          <div class="field" style="flex:1;min-width:200px"><label>新增源整站爬取上限（页）</label><input id="ac-max-pages" type="number" min="1" max="500" step="1" value="${cfg.max_site_pages != null ? cfg.max_site_pages : ''}"><div class="hint">新增网址首次运行主动爬全站全量采题；既有网址按内容指纹增量重扫</div></div>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:14px;margin-top:6px">
          <div class="field" style="flex:2;min-width:300px"><label>站点白名单信任域（每行一个域名；命中不阻断、直采详情，每轮自动整站深采起点）</label><textarea id="ac-whitelist" rows="2" placeholder="gwy.gkzhenti.cn">${esc((cfg.site_whitelist || []).join('\n'))}</textarea></div>
          <div class="field" style="flex:2;min-width:300px"><label>逐站页数额度（每行 域名或网址｜页数；留空=用全局上限）</label><textarea id="ac-site-pages" rows="2" placeholder="gkzhenti.cn|40">${esc(Object.entries(cfg.site_pages_map || {}).map(([k, v]) => `${k}|${v}`).join('\n'))}</textarea></div>
          <div class="field" style="flex:1;min-width:220px"><label>来源优先级（每行：网址|high/medium/low）</label><textarea id="ac-priorities" rows="3" placeholder="https://…|high">${esc((cfg.urls || []).map(u => { const p = (cfg.priorities || {})[u]; return p && p !== 'medium' ? u + '|' + p : u; }).join('\n'))}</textarea><div class="hint">high→medium→low；失败来源自动指数退避后重试</div></div>
          <div class="field" style="min-width:200px"><label>站点详情页规则（手动管理）</label><button class="btn ghost" type="button" onclick="acSiteRulesOpen()">⚙ 管理规则（<span id="ac-sr-count">${_acSiteRules.length}</span>）</button><div class="hint">按域名定制详情页 URL 正则，新增题库站点不用改代码</div></div>
        </div>
      </div>

      <!-- 文档采集 -->
      <div style="border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:14px">
        <div style="font-weight:700;color:var(--wood-deep);margin-bottom:10px">📚 文档采集（已有文档库轮询 → 逐篇 LLM 提取）</div>
        <div class="hint" style="line-height:1.9">
          自动轮询「已上架文档」中启用老师的文档，逐篇 LLM 提取题目入库（来源标记「文档库」）。<br>
          · 内容没变化不重复提取（文档更新后自动重采，省 token）；<br>
          · 提取失败 6 小时内指数退避重试，也可在采集总览「一键补采失败」立即重跑；<br>
          · <b>参与采集的文档 = 文档管理里处于「已上架」的文档</b>，下架即自动不再采集。<br>
          <a href="javascript:acSubTab('ac-content')">→ 到「采集内容配置」查看文档套卷采集标记与溯源</a> ·
          <a href="/admin.html#documents" target="_blank">→ 文档管理</a>
        </div>
      </div>

      <!-- 搜索采集 -->
      <div style="border:1px solid var(--line);border-radius:10px;padding:14px">
        <div style="font-weight:700;color:var(--wood-deep);margin-bottom:10px">🔎 搜索采集（联网搜索 → 抓取 → 提取）</div>
        <div style="display:flex;flex-wrap:wrap;gap:14px">
          <div class="field" style="flex:2;min-width:320px"><label>联网搜索词（每行一个；自动搜索→抓取→提取）</label><textarea id="ac-queries" rows="4" placeholder="例如：2025 国考 行测 真题 言语理解">${esc((cfg.search_queries || []).join('\n'))}</textarea></div>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:14px;margin-top:6px;align-items:flex-end">
          <div class="field" style="min-width:150px"><label>自主采集（零配置找题）</label><select id="ac-autonomous"><option value="0"${cfg.autonomous ? '' : ' selected'}>关闭</option><option value="1"${cfg.autonomous ? ' selected' : ''}>开启</option></select><div class="hint">开启后无需配置网址/搜索词：AI 按课程大类 + 老师学科 + 知识树考点自动生成搜索词轮转探索；未配置任何来源时也会自动生效</div></div>
          <div class="field" style="min-width:130px"><label>每轮自主词数量</label><input id="ac-auto-queries" type="number" min="1" max="100" step="1" value="${cfg.autonomous_queries != null ? cfg.autonomous_queries : ''}"></div>
        </div>
      </div>

    </div>
  </div>`;
  acRefreshControls();
}
