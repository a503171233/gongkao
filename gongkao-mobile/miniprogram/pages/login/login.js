const api = require('../../utils/api');

Page({
  data: { username: '', password: '', loading: false },
  onInput(e) {
    this.setData({ [e.currentTarget.dataset.field]: e.detail.value });
  },
  async submit() {
    const { username, password, loading } = this.data;
    if (loading) return;
    if (!username || !password) {
      wx.showToast({ title: '请输入账号和密码', icon: 'none' });
      return;
    }
    this.setData({ loading: true });
    try {
      const r = await api.login(username, password);
      if (r && r.token) {
        getApp().setAuth(r.token, r);
        wx.switchTab ? wx.reLaunch({ url: '/pages/chat/chat' }) : null;
      } else {
        wx.showToast({ title: '登录失败', icon: 'none' });
      }
    } catch (e) {
      wx.showToast({ title: e.message.slice(0, 40), icon: 'none' });
    }
    this.setData({ loading: false });
  }
});
