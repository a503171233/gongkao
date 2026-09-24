# 网站一（前端）镜像：nginx + 静态 SSE 对话页
# 覆盖：index.html(官网) / chat.html(学习中心主站) / admin.html+admin.js(管理后台) / dashboard.html(运营看板) / share.html(分享落地页) / knowledge.html(前台知识体系) / forum.html(学员论坛) / articles.html(备考文章) / js/ 模块 / css/ 主题
FROM nginx:1.27-alpine
COPY index.html chat.html admin.html admin.js dashboard.html share.html knowledge.html forum.html articles.html /usr/share/nginx/html/
COPY js/ /usr/share/nginx/html/js/
COPY css/ /usr/share/nginx/html/css/
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 3000