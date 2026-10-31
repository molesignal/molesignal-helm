# molesignal-helm

[MoleSignal](https://github.com/molesignal/molesignal) 的 Helm Chart。MoleSignal 是自托管、原生支持
OpenTelemetry 的可观测性平台：日志、指标、链路追踪共用一套 Parquet / DataFusion 引擎。

[English](README.md)

| Chart | 部署内容 |
| --- | --- |
| [`charts/molesignal`](charts/molesignal) | MoleSignal 本体：单 Pod 的 `standalone` 模式，或拆成 router / intake / querier / compactor / alert 的 `distributed` 模式；可选内置 PostgreSQL 与 RustFS，仅用于体验 |

Chart 的版本独立于应用版本，规则见 [版本策略](docs/versioning.md)（英文）。

## 安装

```bash
helm repo add molesignal https://charts.molesignal.com
helm repo update

# 一个 MoleSignal Pod，外加内置的 PostgreSQL 和 RustFS，足够体验
helm install molesignal molesignal/molesignal \
  --namespace molesignal --create-namespace

kubectl --namespace molesignal port-forward svc/molesignal 5080:5080
# 打开 http://127.0.0.1:5080
```

也可以从 OCI 仓库安装：

```bash
helm install molesignal oci://ghcr.io/molesignal/charts/molesignal --version 0.2.0 \
  --namespace molesignal --create-namespace
```

内置的 PostgreSQL 和 RustFS 都是单实例、没有备份。**生产环境**请把它们关掉，改用托管的
PostgreSQL 17 和 S3 存储桶，写法见 [`examples/production.yaml`](examples/production.yaml)。

全部可配置项、chart 管理的密钥（尤其是必须自行备份的 cipher key）、对外暴露 UI 与 OTLP、升级与卸载，
见 [chart README](charts/molesignal/README.md)（英文）。

## 几点要先知道

- **备份 cipher key。** 它用来加密字段，丢了就无法再读取这些字段，即使数据库是从备份恢复的。
  chart 首次安装时生成它并保存在 Secret 里，卸载 release 时 Secret 会被保留。
- **Argo CD 等执行 `helm template` 的工具**无法读取集群里已有的 Secret，自动生成的值每次同步都会变。
  请显式设置 `secrets.cipherKey`、`secrets.clusterToken`，或用 `secrets.existingSecret` 指向你自己管理的 Secret。
- **`/metrics` 没有鉴权**，和 UI、API 在同一个端口（5080）。通过 Ingress 暴露时请屏蔽它，
  nginx 的写法见 [`examples/ingress-nginx.yaml`](examples/ingress-nginx.yaml)。
- **Ingress 后面拿不到真实客户端地址**，所有用户都算成控制器的地址。安装后在 `_sys` 组织的
  **设置 → 客户端 IP 识别** 里把控制器 Pod 的网段列为受信任的代理。
- 应用对 S3 只使用你配置的密钥；没有密钥时回退到节点的实例角色。**不支持 IRSA / EKS Pod Identity。**

## 开发

需要 `helm` 3.8+、`yq`（mikefarah v4）和 Python 3.11+。

```bash
make check     # lint、文档检查、渲染测试、变异测试，全部离线
make package   # 打包到 ./dist
```

测试不会连接任何集群（每次调用都把 `KUBECONFIG` 指向 `/dev/null`）。CI 还会用 kubeconform 校验清单，
并在 chart 变更时和每周一次在 kind 集群里真实安装。详见 [CONTRIBUTING](CONTRIBUTING.md)。

## 发布与托管

合并一个升级了 chart 版本号的 PR，就会把 chart 发布到 GitHub Pages（传统 Helm 仓库）和 ghcr.io（OCI）。
首次需要的仓库设置、以及可选的自定义域名，见 [publishing](docs/publishing.md)（英文）。

## 许可证

Apache License 2.0，与应用一致。见 [LICENSE](LICENSE)。
