# Transition History CSV Export Design

Mục tiêu là trích xuất lịch sử chuyển trạng thái dạng CSV cho toàn bộ thuê bao trong `database.db:danhba`, nhưng chỉ giữ các thuê bao đã có lịch sử dao động đáng chú ý sau khi loại trừ sự cố diện rộng.

## Yêu cầu

- Tập thuê bao đầu vào lấy từ toàn bộ bản ghi `danhba`.
- Lịch sử trạng thái dựng từ dữ liệu đo thô trong `onu_measurements.db:onu_measurements`.
- Phát hiện transition khi hai mẫu đo hợp lệ liên tiếp đổi từ `ON -> OFF` hoặc `OFF -> ON`.
- Loại khỏi lịch sử mọi outage thuộc diện rộng.
- Chỉ giữ thuê bao có đồng thời:
  - ít nhất 3 lần `ON -> OFF`
  - ít nhất 3 lần `OFF -> ON`
- CSV đầu ra ở dạng nhiều dòng trên mỗi mã, mỗi transition là một dòng.

## Nguồn dữ liệu

- `database.db:danhba`
  - danh mục thuê bao gốc
  - khóa nối là `danhba.sub`
- `onu_measurements.db:onu_measurements`
  - chuỗi mẫu đo thô theo thời gian
  - khóa nối là `"Cổng"` tương ứng với `danhba.sub`
- `onu_measurements.db:wide_area_alerts`
  - xác định batch và tập thuê bao thuộc sự cố rộng
- `onu_measurements.db:outage_alerts`
  - bổ sung cờ `suppressed_by_wide_area` để xác nhận một outage start thuộc wide-area

## Thiết kế xử lý

Script sẽ tải danh mục thuê bao từ `danhba`, sau đó lấy toàn bộ mẫu đo liên quan từ `onu_measurements`, chuẩn hóa `onuStatusStr` về `ON`, `OFF`, hoặc `UNKNOWN`, và duyệt tuần tự theo từng thuê bao.

Khi trạng thái hợp lệ đổi từ `ON` sang `OFF`, script mở một outage segment. Segment đó được đánh dấu `wide_area` nếu batch bắt đầu outage chứa thuê bao trong `wide_area_alerts`, hoặc nếu `outage_alerts` cho cùng `subscriber_key` và `batch_id` có `suppressed_by_wide_area = 1`.

Khi trạng thái đổi ngược từ `OFF` sang `ON`, script đóng segment đang mở. Nếu segment đã bị đánh dấu `wide_area`, loại cả hai transition `ON -> OFF` và `OFF -> ON` của segment đó. Nếu không, giữ cả hai transition như lịch sử hợp lệ.

Các mẫu `UNKNOWN` không tạo transition mới và không reset trạng thái hợp lệ gần nhất.

## Định dạng CSV

Mỗi dòng là một transition hợp lệ, với các cột chính:

- `ma_tb`
- `ten_tb`
- `subscriber_key`
- `doi_vt`
- `ten_nvkt_db`
- `transition_type`
- `from_status`
- `to_status`
- `from_batch_id`
- `to_batch_id`
- `from_time`
- `to_time`
- `transition_time`
- `on_to_off_count`
- `off_to_on_count`
- `total_valid_transitions`

Các cột đếm là tổng hợp theo thuê bao và được lặp lại ở mọi dòng để tiện lọc/sort trong Excel.

## Kiểm thử

- Xác minh phát hiện đúng transition thô từ chuỗi đo.
- Xác minh loại cả cặp transition của outage wide-area.
- Xác minh CSV chỉ xuất các thuê bao đạt ngưỡng `>= 3` cho cả hai chiều.
