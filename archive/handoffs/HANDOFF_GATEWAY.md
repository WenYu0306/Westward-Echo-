# 西渡 → 统一网关 交接文档

> 最后更新：2026-08-18（Claude 窗口交接）
> 目的：下一个窗口接手「三个项目共用一台服务器、统一 Caddy 网关」这件事

---

## 一、背景：三个项目要上同一台服务器

- 服务器：腾讯云 **62.234.134.33**（2核4G，北京）
- 三个项目都在这台服务器：
  - **西渡**（已上线，westwardecho.com）
  - **析**（待上线，analyze.westwardecho.com）
  - **铸文**（待上线，forge.westwardecho.com）

## 二、核心矛盾：三个 caddy 抢 80/443 端口

现在三个项目各自的 docker-compose 里都带一个 caddy，都要占宿主机 80/443。三个 caddy 只能活一个，其余起不来。

## 三、已决定的方案：方案 A —— 独立统一网关

**用户拍板选方案 A**：三个项目各自的 compose **摘掉 caddy**，单独起一个「网关」compose，一个 Caddy 管三个域名。

```
统一网关（一个 Caddy）
  ├─ westwardecho.com          → 西渡 api（容器内 8000）
  ├─ analyze.westwardecho.com  → 析 api（容器内 8001）
  └─ forge.westwardecho.com    → 铸文 api（容器内 8900）
```

## 四、三个项目的端口（已核实）

| 项目 | api 端口 | 当前宿主机映射 |
|------|---------|--------------|
| 西渡 | 容器内 **8000** | ❌ 未暴露宿主机（靠自己的 caddy 转） |
| 析 | **8001** | ✅ 已映射 `8001:8001` |
| 铸文 | **8900** | ✅ 已映射 `8900:8900` |

## 五、要做的事（下一个窗口执行）

### 1. 三个项目各自摘掉 caddy

**西渡**（本项目）：
- `docker-compose.yml` 删掉 `caddy` 服务（第76-88行）和 `caddy_data`/`caddy_config` 两个 volume（第91-92行）
- 删掉 `Caddyfile`（或保留但不被 compose 用）

**析**、**铸文**：同样摘掉各自的 caddy（由铸文窗口、析窗口各自处理）

### 2. 让三个 api 暴露到宿主机（供网关转发）

- 西渡 api：加 `ports: - "8000:8000"`（当前没有）
- 析：已有 `8001:8001` ✅
- 铸文：已有 `8900:8900` ✅

### 3. 新建统一网关

单独一个目录（或 compose 文件），一个 Caddy，Caddyfile 写：

```
westwardecho.com {
    reverse_proxy <宿主机IP>:8000
}
analyze.westwardecho.com {
    reverse_proxy <宿主机IP>:8001
}
forge.westwardecho.com {
    reverse_proxy <宿主机IP>:8900
}
```

注意：如果统一网关是独立 compose，它和三个项目不在同一个 docker 网络，要用 `host.docker.internal` 或宿主机 IP 转，而不是 docker 服务名。

## 六、西渡改动要点（本项目范围）

1. 删 caddy 服务 + caddy volume（docker-compose.yml）
2. api 加 `ports: - "8000:8000"`
3. 删除或弃用 `Caddyfile`

## 七、注意（容易踩的坑）

1. **西渡的 redis 也占了宿主机 6379**，铸文的 compose 里也有 redis。如果三个项目各自起 redis，也会撞 6379。要么共用 redis，要么改端口。
2. **Chroma**：西渡有独立的 chroma 容器，只有西渡用，不冲突。
3. **Caddy 自动 HTTPS**：统一网关的 Caddy 会自动申请三个域名的 Let's Encrypt 证书，需要 DNS 已经指向服务器。

---

*这份是「统一网关方案」的专项交接。西渡的整体状态看 `HANDOFF.md`。*
