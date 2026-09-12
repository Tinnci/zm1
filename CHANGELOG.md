# Changelog / 更新记录

## 0.3.0 - 2026-09-13

- Receive unsolicited UDP measurements continuously, serialize requests per
  device and retry only read-only queries.
- Track each field's real receipt time in immutable observations. Expire missing
  measurements independently after 300 seconds, including during polling backoff.
- Publish every value, source and availability change immediately. Coalesce
  repeated identical publications for up to 60 seconds and flush the final report.
- Remove unsupported TVOC, CO2 and eCO2 entities. Disable communication-only
  diagnostics by default for new registrations while preserving existing choices.
- Fix HACS archive layout, use pytest during release verification, verify the
  selected tag and synchronize package metadata before writing the archive.

移除的三类气体实体没有真实硬件测量依据。温度、湿度、甲醛和 PM2.5 保留原始精度，
UDP 匹配回复仍仅是报告状态，不因传输成功而升级为物理确认。
