# OLT Port Issue CSV Logging Design

## Goal

Ghi lại danh sách cổng bị `filtered` và `error` trong từng batch đo OLT ra file CSV riêng để có thể tra cứu lại sau khi batch kết thúc.

## Scope

- Chỉ thay đổi luồng đo trong `app.py`.
- Không đổi cấu trúc bảng SQLite hiện có.
- Không thay đổi logic alert/post-processing.

## Design

- Tạo thư mục log CSV theo batch, mặc định dưới `logs/measurement_batches/`.
- Mỗi batch có một file tên `<batch_id>_port_issues.csv`.
- Mỗi dòng chứa:
  - `logged_at`
  - `batch_id`
  - `status`
  - `olt_name`
  - `device_ip`
  - `frame`
  - `slot`
  - `port`
  - `port_label`
  - `reason`
- Ghi log ngay tại `download_single_port` vì đây là nơi biết chính xác nguyên nhân `filtered` hoặc `error`.
- Dùng lock riêng cho file CSV để an toàn khi nhiều worker ghi song song.

## Error Handling

- Nếu chưa có file thì tạo mới và ghi header.
- Nếu đã có file thì append thêm dòng.
- Nếu ghi log CSV lỗi, in cảnh báo ra console nhưng không làm hỏng batch đo.

## Testing

- Thêm test cho helper ghi CSV.
- Thêm test cho nhánh `filtered` ghi log đúng.
- Thêm test cho nhánh `error` ghi log đúng khi JSON không hợp lệ.
