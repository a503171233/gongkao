// 公考学习小程序 · 全局配置
// 后端：与 App 共用同一 FastAPI 服务（多端复用核心资产），改 BASE_URL 即可切换环境
App({
  globalData: {
    // 生产站点（HTTPS，小程序后台需在 request 合法域名中配置该域名）
    baseUrl: 'https://a2a0641667069c341.app.workbuddy.host',
    token: '',          // 登录态（gk_token 同源契约）
    userInfo: null
  },
  onLaunch() {
    // 恢复本地登录态（与 Web/App 的 localStorage gk_token 语义一致）
    const token = wx.getStorageSync('gk_token');
    if (token) {
      this.globalData.token = token;
      this.fetchMe();
    }
  },
  // 拉取当前用户（/api/me），失败则清除本地 token
  fetchMe() {
    const self = this;
    wx.request({
      url: this.globalData.baseUrl + '/api/me',
      header: { Authorization: 'Bearer ' + this.globalData.token },
      success(res) {
        if (res.statusCode === 200 && res.data && res.data.user_id) {
          self.globalData.userInfo = res.data;
        } else {
          self.clearAuth();
        }
      },
      fail() { self.clearAuth(); }
    });
  },
  setAuth(token, user) {
    this.globalData.token = token;
    this.globalData.userInfo = user;
    wx.setStorageSync('gk_token', token);
  },
  clearAuth() {
    this.globalData.token = '';
    this.globalData.userInfo = null;
    wx.removeStorageSync('gk_token');
  }
});
