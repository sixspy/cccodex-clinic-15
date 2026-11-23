import { api, chip, escapeHtml } from '../api.js';

function archiveCard(badge, items) {
  const b = badge || { status: '未归档' };
  return `
    <div class="card">
      <h2>审计归档 <span id="archBadge">${chip(b.status)}</span></h2>
      <p class="muted">甲路物化包：提交即刻把当前审计流水钉入只读包并盖键指纹；读轨只认物化包，钉点之前拒绝回刷。</p>
      <div class="grid">
        <div class="stat"><div class="n">${escapeHtml(b.archive_key || '-')}</div><div class="l">归档徽标 · 当前钉点键号</div></div>
        <div class="stat"><div class="n">${escapeHtml(b.fingerprint_short || '-')}</div><div class="l">键指纹短码</div></div>
        <div class="stat"><div class="n">${b.event_count ?? '-'}</div><div class="l">本包钉住条数</div></div>
        <div class="stat"><div class="n">${b.last_event_id ? '#' + b.last_event_id : '-'}</div><div class="l">钉至行次</div></div>
      </div>
      <div class="row">
        <input id="archNote" placeholder="归档备注（可空）" style="max-width:260px" />
        <button id="archSubmit">提交归档（即刻写定入库）</button>
        <button id="archReload" class="ghost">刷新</button>
      </div>
      <div id="archMsg" class="muted" style="margin:8px 0"></div>
      <table>
        <thead><tr><th>归档键号</th><th>指纹短码</th><th>条数</th><th>落点行次</th><th>时间</th><th>操作</th></tr></thead>
        <tbody>
          ${(items || []).map((a) => `
            <tr>
              <td><code>${escapeHtml(a.archive_key)}</code></td>
              <td>${escapeHtml((a.fingerprint || '').slice(0, 12))}</td>
              <td>${a.event_count}</td>
              <td>#${a.first_event_id} - #${a.last_event_id}</td>
              <td class="muted">${escapeHtml(a.created_at || '')}</td>
              <td><button class="ghost" data-arch="${a.id}">详情</button></td>
            </tr>`).join('') || '<tr><td colspan="6" class="muted">暂无归档，提交后此处列出归档队列。</td></tr>'}
        </tbody>
      </table>
      <div id="archDetail" class="detail-box">归档详情读轨只认物化包；点「详情」展开落点行次。</div>
    </div>`;
}

async function loadArchiveDetail(el, id) {
  const box = el.querySelector('#archDetail');
  box.textContent = '加载中…';
  try {
    const d = await api(`/api/archives/${id}`);
    box.textContent = JSON.stringify({
      归档键号: d.archive_key,
      键指纹: d.fingerprint,
      钉住条数: d.event_count,
      落点行次: (d.items || []).map((it) => ({
        行次: it.event_id,
        时间: it.created_at,
        动作: it.action,
        实体: `${it.entity_type}#${it.entity_id}`,
        详情: it.detail,
      })),
    }, null, 2);
  } catch (e) {
    box.textContent = '错误：' + e.message;
  }
}

async function mountArchive(el) {
  let data;
  try {
    data = await api('/api/archives');
  } catch (e) {
    el.querySelector('#archMsg').textContent = e.message;
    return;
  }
  const host = el.querySelector('#archiveHost');
  host.innerHTML = archiveCard(data.badge, data.items);
  const msg = host.querySelector('#archMsg');
  host.querySelector('#archSubmit').onclick = async () => {
    try {
      const note = host.querySelector('#archNote').value;
      const arch = await api('/api/archives', {
        method: 'POST',
        body: JSON.stringify({ note }),
      });
      msg.className = 'muted';
      msg.textContent = `已写定入库 ${arch.archive_key} · 钉住${arch.event_count}条 · 指纹${arch.fingerprint.slice(0, 12)}`;
      await mountArchive(el);
    } catch (e) {
      msg.className = 'err';
      msg.textContent = e.message;
    }
  };
  host.querySelector('#archReload').onclick = () => mountArchive(el);
  host.querySelectorAll('[data-arch]').forEach((btn) => {
    btn.onclick = () => loadArchiveDetail(host, btn.dataset.arch);
  });
}

export async function renderSettings(el) {
  const data = await api('/api/settings');
  const items = data.items || [];
  const snapshot = data.snapshot || {};

  el.innerHTML = `
    <div class="card">
      <h2>台参数</h2>
      <p class="muted">控制质控自动扫描开关、补采最大次数、配额告警与对账口径标签等。修改会写审计。</p>
      <div class="grid">
        <div class="stat"><div class="n">${snapshot.backfill_max_attempt || '-'}</div><div class="l">补采最大次数</div></div>
        <div class="stat"><div class="n">${snapshot.qc_auto_scan || '-'}</div><div class="l">自动质控</div></div>
        <div class="stat"><div class="n">${snapshot.site_quota_warn || '-'}</div><div class="l">配额告警阈值</div></div>
        <div class="stat"><div class="n">${escapeHtml((snapshot.clinic_timezone || '-').slice(0, 12))}</div><div class="l">时区</div></div>
      </div>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>键</th><th>说明</th><th>当前值</th><th>新值</th><th>操作</th></tr></thead>
        <tbody>
          ${items.map((s) => `
            <tr>
              <td><code>${escapeHtml(s.key)}</code></td>
              <td>${escapeHtml(s.label)}</td>
              <td><strong>${escapeHtml(s.value)}</strong></td>
              <td><input data-key="${escapeHtml(s.key)}" value="${escapeHtml(s.value)}" /></td>
              <td><button data-save="${escapeHtml(s.key)}">保存</button></td>
            </tr>`).join('')}
        </tbody>
      </table>
      <div id="msg" class="muted" style="margin-top:10px"></div>
    </div>
    <div id="archiveHost"></div>`;

  const msg = el.querySelector('#msg');
  el.querySelectorAll('[data-save]').forEach((btn) => {
    btn.onclick = async () => {
      const key = btn.dataset.save;
      const input = el.querySelector(`input[data-key="${key}"]`);
      try {
        const updated = await api('/api/settings/update', {
          method: 'POST',
          body: JSON.stringify({ key, value: input.value }),
        });
        msg.className = 'muted';
        msg.textContent = `已保存 ${updated.key} = ${updated.value}`;
        await renderSettings(el);
      } catch (e) {
        msg.className = 'err';
        msg.textContent = e.message;
      }
    };
  });

  await mountArchive(el);
}
