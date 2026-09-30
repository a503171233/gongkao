const api = require('../../utils/api');
const app = getApp();

// SSE 流式问答页（精简版：核心问答链路，复用 Web 端 /api/ask 契约）
Page({
  data: {
    messages: [],   // {role:'user'|'bot', text, streaming}
    query: '',
    sending: false
  },
  onShow() {
    if (!app.globalData.token) {
      wx.redirectTo({ url: '/pages/login/login' });
    }
  },
  onInput(e) { this.setData({ query: e.detail.value }); },

  async send() {
    const q = (this.data.query || '').trim();
    if (!q || this.data.sending) return;
    const msgs = this.data.messages.concat(
      { role: 'user', text: q },
      { role: 'bot', text: '', streaming: true }
    );
    this.setData({ messages: msgs, query: '', sending: true });
    this.scrollBottom();

    // 拒答话术检测（与 Web 端 REJECT_MARK 一致）
    let full = '';
    const update = () => {
      const key = 'messages[' + (msgs.length - 1) + '].text';
      const patch = {};
      patch[key] = full;
      this.setData(patch);
      this.scrollBottom();
    };
    api.askSSE(
      { query: q, teacher_id: 'T001', stream: true, session_id: '' },
      (t) => { full += t; update(); },
      () => {
        const k1 = 'messages[' + (msgs.length - 1) + '].streaming';
        const patch = {}; patch[k1] = false;
        this.setData(Object.assign(patch, { sending: false }));
      },
      (err) => {
        if (!full) full = '⚠️ ' + err.message;
        const k1 = 'messages[' + (msgs.length - 1) + '].text';
        const k2 = 'messages[' + (msgs.length - 1) + '].streaming';
        const patch = {}; patch[k1] = full; patch[k2] = false;
        this.setData(Object.assign(patch, { sending: false }));
      }
    );
  },
  scrollBottom() {
    wx.nextTick(() => wx.pageScrollTo({ scrollTop: 999999, duration: 120 }));
  }
});
