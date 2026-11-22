# 上传个人 GitHub 的准备说明

## 已准备的内容

本地 `publish/github-source` 是按清单导出的干净源码，适合上传仓库。`publish/release-v5` 是成品附件。不要把包含旧版本 EXE、构建目录、缓存和本地开发笔记的整个工作目录上传。

仓库建议名称：`lan-transfer`。建议描述：`Windows 局域网文件共享与互传工具：手机扫码浏览电脑目录、播放媒体，无需安装手机 APP。`

## 发布前由作者决定

- 使用哪个个人账号及仓库名称。
- 仓库公开还是私有。
- 是否授予开源许可，以及具体授权条款。此整理未替作者添加 LICENSE。

## 推荐上传方式

1. 登录自己的 GitHub，在个人账号下新建仓库。
2. 可用 GitHub Desktop 把 `publish/github-source` 作为本地仓库初始化并发布，或将里面的文件与目录通过 Git 上传。
3. 若用网页上传，展开源码 ZIP 后上传其中内容；不要只把 ZIP 放到仓库，也不要漏掉 `.gitignore` 和 `.gitattributes`。
4. 先检查待上传列表：应包含 Python 源码、tests、docs、依赖文件、README 和打包配置，不应包含 EXE、缓存、访问码或本地共享配置。
5. 在 Releases 新建 `v5.0.0`，粘贴 `docs/RELEASE-v5.md` 的内容，添加 `release-v5` 内的成品和说明。
6. 确认公开范围与授权选择后再发布。这里没有创建远端仓库、推送代码或发布 Release。

## 如果使用命令行

在干净源码目录执行初始化和本地提交后，将你自己创建的仓库 URL 添加为 `origin` 再推送。账号登录在 GitHub 的正常授权界面完成，不把密码或 token 写进文件。

```powershell
git init -b main
git add .
git diff --cached --stat
git commit -m "Prepare File Transfer V5 source release"
```

上面只初始化与提交本地仓库，不包括远端地址和推送命令。仓库创建后使用 GitHub 页面提供的实际远端地址。

## 本次整理记录

仅修改文档和发布配套文件，保留现有程序逻辑和成品。依赖依据本机包元数据记录。没有运行测试、启动 EXE、打开浏览器、创建远端仓库或选择开源许可证。

导出的源码 ZIP 只是整理快照。后续修改源码后需要重新导出，Git 本身不会自动更新 ZIP 或成品附件。
