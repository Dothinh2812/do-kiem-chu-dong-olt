# Port Precheck And PORT_DOWN Design

## Goal

Bổ sung bước kiểm tra trạng thái port OLT trước khi đo chi tiết ONU trên từng cổng. Nếu port `Up` thì tiếp tục đo ONU như hiện tại. Nếu port `Down` thì không gọi API đo chi tiết ONU cho port đó, mà suy ra trạng thái diện rộng cho toàn bộ thuê bao nằm trên port dựa vào `danhba.db`.

## Confirmed Requirements

- Danh sách cổng đầu vào vẫn được sinh từ `danhba` trong `database.db`.
- Trạng thái port được lấy từ endpoint `GET /Linetest/Test/GetL2PortListBySlot`.
- Nếu port `Down`:
  - chỉ xử lý khi port có thuê bao trong `danhba.db`
  - ghi measurement rows vào `onu_measurements`
  - lưu `onuStatusStr = "PORT_DOWN"`
  - đưa thuê bao vào `current_off_snapshot.json`
  - đánh dấu đó là sự cố diện rộng
- Nếu port `Down` nhưng không có thuê bao thì bỏ qua.
- Cần phân biệt `PORT_DOWN` với `OFF` đo trực tiếp từ ONU.

## Existing Context

- `app.py` hiện sinh danh sách port từ `database.db`, chạy batch đo song song và ghi vào `onu_measurements`.
- `cts_port_status_scan.py` đã có luồng riêng để login CTS, quét port status và export offline, nhưng chưa nối vào batch measurement.
- `alert_engine.py`, `current_off_snapshot.py`, `transition_history_export.py` đang chuẩn hóa phần lớn trạng thái theo logic `ON/OFF/UNKNOWN`.

## Recommended Approach

Giữ bước port precheck trong cùng batch measurement flow để một batch luôn có quyết định thống nhất:

1. Lấy danh sách port tasks từ `database.db` như hiện tại.
2. Với mỗi port task, hỏi trạng thái port trước.
3. Nếu `ifStatus == "Up"`:
   - gọi `GetListByPonPortAsync`
   - xử lý như luồng hiện tại
4. Nếu `ifStatus == "Down"`:
   - không gọi `GetListByPonPortAsync`
   - tra thuê bao thuộc port đó từ `danhba`
   - nếu có thuê bao thì ghi measurement rows với `onuStatusStr = "PORT_DOWN"`
   - đưa các thuê bao này vào nhánh wide-area trong hậu xử lý
5. Nếu port trả trạng thái khác `Up/Down`, hoặc request port-status lỗi:
   - log lại như issue riêng
   - không giả định `PORT_DOWN`

## Data Model Changes

Không thêm cột mới vào `onu_measurements`.

- Dùng giá trị mới trong `onuStatusStr`: `PORT_DOWN`
- `PORT_DOWN` là trạng thái đo gián tiếp từ port, không phải `OFF` ONU

Các measurement rows do `PORT_DOWN` sinh ra vẫn cần:

- `Cổng`
- `batch_id`
- `frameNo`, `slotNo`, `portNo`, `onuIndex`
- metadata thuê bao như hiện tại qua `danhba.sub`
- `NgayDo`, `ThoiGianDo`

Các trường đo trực tiếp ONU như `oltPowerRx`, `onuPowerRx`, `onuLastOff`, `onuLastOn`, `onuSN` có thể để trống khi nguồn là `PORT_DOWN`.

## Port To Subscriber Expansion

Cần thêm helper tra toàn bộ thuê bao theo parent port từ `danhba.sub`, ví dụ:

- `HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:1`
- `HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:2`

đều thuộc parent port:

- `HNI.STY.XSZ.OLT.ZT.1.1_1-1-11`

Helper này là nền tảng để khi port `Down`, hệ thống có thể sinh measurement rows cho mọi ONU/subscriber của port đó mà không cần gọi CTS detail endpoint.

## Alert And Snapshot Behavior

`PORT_DOWN` phải được hiểu là sự cố diện rộng ngay tại batch hiện tại.

Yêu cầu hành vi:

- `current_off_snapshot.json` vẫn chứa các thuê bao bị ảnh hưởng.
- trạng thái hiển thị phải giữ được nguồn là `PORT_DOWN`, không bị normalize mất về `OFF`.
- các thuê bao này phải được đánh dấu suppressed/wide-area tương ứng để không bị xử lý như OFF đơn lẻ.
- logic tạo `wide_area_alerts` cần chấp nhận nguồn dữ liệu đến từ `PORT_DOWN`, không phụ thuộc hoàn toàn vào `onuLastOff` clustering hiện tại.

## Logging

Nên có log batch riêng cho quyết định port-precheck, tách với log lỗi request:

- `up`
- `down_with_subscribers`
- `down_without_subscribers`
- `port_status_error`
- `unexpected_status`

Điều này giúp phân biệt:

- sự cố nghiệp vụ thật do port down
- lỗi kỹ thuật khi không lấy được trạng thái port

## Error Handling

- Nếu request port-status trả HTML/login page: retry qua session refresh theo cùng chiến lược hiện có, hoặc fail có log rõ nguyên nhân.
- Nếu slot scan không tìm thấy đúng port cần tra: ghi `port_status_error`, không suy diễn `PORT_DOWN`.
- Nếu `danhba` không có subscriber nào cho port `Down`: bỏ qua, không ghi measurement.
- Nếu luồng port-precheck lỗi ở một port: không làm chết toàn batch.

## Testing Scope

Cần test ít nhất các tình huống:

- Port `Up` đi qua nhánh đo ONU hiện có.
- Port `Down` có thuê bao:
  - không gọi API detail ONU
  - ghi measurement rows với `PORT_DOWN`
  - snapshot đánh dấu diện rộng
- Port `Down` không có thuê bao:
  - không ghi measurement
  - không tạo snapshot
- Port-status endpoint lỗi hoặc trả non-JSON:
  - ghi issue phù hợp
  - không giả lập `PORT_DOWN`
- Alert engine hiểu `PORT_DOWN` là wide-area source.
- Export/history không làm hỏng các logic đang dựa vào `ON/OFF`.
