# 设备封页交班

设备把几次曝光的原包放在同一缓存页。回收一页会同时清掉页内原包、曝光明细和边沿。采集员仍可逐件接管，质检仍使用已经接管的原值。页是否齐备决定设备能否回收。页槽回收后可再次使用，夜班仍按原物理页领件。

## 设备入口

沿用 `OfflineBench` 的固定装夹、发令和开闭光。以下是扩展的接口。实现使用 Python 和原 SQLite 站库。每台设备仍由一个调用者顺序驱动。站库可关闭重开，确认续办时使用仍在线的同一台架。设备断电后的缓存重建不在本次交班范围内。

- 在 `station.bench.recording` 中提供不可变值对象 `PageKey(device, slot, generation)`、`PageReply(page, reply)` 和 `PageSeal(page, exposures)`。`device` 是台架原命名空间，`slot` 是非负整数，`generation` 是正整数，`reply` 是原有 `RawReply`，`exposures` 是非空、无重复的曝光号元组。整页确认使用 `station.bench.confirmation` 中的不可变值对象 `PageConfirmation(page, exposures)`。值对象按字段相等。
- `bench.open_page(slot)` 返回该槽新一代的 `PageKey`。同一槽只可有一个尚未回收的物理页，代次从 1 递增。占用时拒绝再开。
- `bench.record(exposure_id, raw_code, page=page)` 将已闭合的曝光原包登记到该台架的开放页并返回 `PageReply`。曝光首次登记时固定归属。原页原值重投复用原包，异值、改页、剥去已有页身份或向封定页追加新成员均拒绝。原 `record` 不带页参数时仍可登记普通曝光。`bench.deliver_reply(exposure_id)` 返回实际登记的包，页未封定时也可交付页包。
- `bench.seal_page(page)` 冻结该页实际登记的成员，按首次登记顺序返回 `PageSeal`。空页不能封定。已封定页重投同一封页动作仍返回原声明。`bench.deliver_seal(page)` 读取设备已封定的原声明。设备留存已回收页的原声明及原确认，槽复用后仍按原 `PageKey` 区分。
- `bench.confirm(exposure_id)` 及其底层旧单曝确认拒绝回收页内曝光。整页回收成功后，页内包已无法从 `deliver_reply` 获取。回收点前应保有整页，回收点后应一次清除整页并可复领该页的原确认。

## 接管、续办和领取

- `capture = station.attach(bench)`。`capture.admit(PageReply)` 返回原有 `AdmissionReceipt`，与 `capture.admit(RawReply)` 共用原码和质检来源。`capture.admit_seal(seal)` 接管设备发出的封页声明并返回该 `PageSeal`。首次接管须与当前绑定设备的实际原包或声明相符。两种输入可任意先后送达，原样重投复领，错页、错设备、错成员或异内容拒绝且保全已提交进度。已回收页的相同来件可依据站库原记录复领。去掉页身份的包不能当作普通曝光接管。
- 原包与其页归属一并持久接管。接管写入失败时，原件仍可从源取得。旧单曝待办不能回收页内材料。站库提交后，原件即可继续参加原双头质检，无须等待整页确认。
- `capture.acknowledgements.confirm_page(page)` 办理或续办指定物理页的确认。站库已知页但未齐时返回 `None`。齐备时返回设备的 `PageConfirmation`。只有封页声明和每件原包都已持久接管才可整页回收。部分接管证据可以交接，整页释放由显式页确认调用驱动。交接采用已绑定的台架与站库，两端内部交证接口由实现决定。旧 `confirm_exposure` 和 `retry_pending` 保留原单曝语义。
- 可用原故障钩子 `admit.before_commit` 拦下页原包接管，或用 `source.before_confirm` 和 `source.after_reclaim` 分别在整页回收前及回收生效后注入失败。回收生效后失答，允许关闭重开站库并重绑同一在线源。重试必须依据原页，返回原 `PageConfirmation`，不得再次回收或作用于复用槽的新页。回收前失败保有整页。
- `station.collect_page(page)` 为只读查询，无须绑定设备。站库仅收到原包或声明、仍未齐备时返回 `None`。齐备时返回 `(seal, readings)`，其中 `readings` 是按原声明顺序排列的 `RawReading` 元组，含曝光身份、头位、原码及原接管回执。齐备可在设备确认前查询，回收后和站库重开后仍返回同样的原件。站库从未接管过该页任一输入时，页确认和页查询均抛出 `KeyError`。非法输入、身份或状态冲突沿用 `ValueError`，未知设备端身份沿用 `KeyError`，写入错误或注入失败向调用者传播。

## 样例与交班核验

`page_fixture.py` 提供同一清册下 P、Q 两片的确定性曝光输入。运行 `python3 -B page_fixture.py` 可先查看原单曝光模式的原据和质检结果。封页验证使用同样的装夹和原码。

1. 台架 `page-demo` 的槽 4 打开第一页 G1，依次登记 P 的左包 A、右包 B，原码为 3、7。先投 B 和重投 B，再送声明。也核对声明先送的顺序。尝试单曝光回收时 A、B 仍可取。A 首次接管提交失败时，G1 仍待齐且两包尚在设备。A 补交成功后，页领取按 A、B 顺序给出原件与各自原回执。即使尚未请求回收，原质检也已可签得 10、12 μm，差值 2 μm，合格。
2. 请求回收 G1，在 `source.after_reclaim` 失答。此时 G1 两包都已回收，槽 4 可用于 G2，登记 Q 的原码 4、6。关闭重开站库并重绑还在线的设备，按 G1 续办须返回 G1 原确认，G2 两包仍可取。将 G2 的包错绑给 G1 应拒绝。再停源并重开站库，按 G1 与原质检成果号领件，核对曝光、顺序、原码及相关回执。

交付仍用 `.pointwise-runtime.json`。清单的 `version` 为 1，`kind` 为 `cli`，`setup` 为准备命令的 argv 列表，`verify` 和 `demo` 各为一条命令的 argv。验证入口要运行原回归和实际封页核验，断言失败以非零退出。演示入口使用真实业务调用呈现乱序接管、同槽续认和停源重开领件。
