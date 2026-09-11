# Tài liệu API gửi tin nhắn ZaloCRM

Tài liệu này mô tả các API công khai để ứng dụng bên ngoài gửi tin nhắn Zalo qua ZaloCRM. Các API dùng `X-API-Key`, tự giới hạn theo tổ chức sở hữu API key, và không dùng JWT.

## 1. Quy ước chung

Base URL:

```text
https://your-domain
```

Header bắt buộc:

```http
X-API-Key: your-api-key
Content-Type: application/json
```

API key được tạo trong màn hình **API & Webhook** của ZaloCRM. Nếu thiếu hoặc sai key, hệ thống trả về:

```json
{ "error": "API key required" }
```

hoặc:

```json
{ "error": "Invalid API key" }
```

Mọi endpoint bên dưới nằm dưới prefix `/api/public`.

## 2. Gửi tin nhắn cá nhân theo mã thuê bao

Endpoint này dành cho ứng dụng ngoài chỉ biết mã thuê bao. ZaloCRM sẽ tìm contact theo `maTb`, lấy định danh Zalo đã import, chọn tài khoản Zalo gửi tương ứng, render nội dung cá nhân hóa rồi gửi ngay.

Nếu contact chưa có Zalo UID người nhận nhưng có số điện thoại, hệ thống dùng đúng tài khoản Zalo gửi đã map với `maTb` để lookup UID từ số điện thoại. Nếu lookup thành công với số danh bạ, UID được lưu lại vào `Contact.zaloUid` và `ContactMessagingIdentity.recipientExternalId`; nếu lookup từ `reportPhone`, UID được lưu vào identity phụ riêng. Tin nhắn luôn được gửi bằng chính tài khoản vừa lookup.

```http
POST /api/public/messages/send-by-ma-tb
```

### Request body

Gửi cho một mã thuê bao:

```json
{
  "maTb": "TB001",
  "reportPhone": "0987654321",
  "content": "Xin chào {ho_ten}, mã thuê bao {ma_tb} tại {dia_chi} cần được hỗ trợ."
}
```

`reportPhone` dùng khi khách báo hỏng bằng số điện thoại khác số đang lưu trong danh bạ thuê bao. Khi truyền trường này, hệ thống gửi tới Zalo UID lookup từ `reportPhone`, không dùng `Contact.phone`/`Contact.zaloUid` của số danh bạ.

Gửi ngay cho nhiều mã thuê bao:

```json
{
  "maTbs": ["TB001", "TB002", "TB003"],
  "content": "Xin chào {ho_ten}, mã thuê bao {ma_tb} của quý khách đã được tiếp nhận."
}
```

Trường dữ liệu:

| Trường | Kiểu | Bắt buộc | Mô tả |
| --- | --- | --- | --- |
| `maTb` | string | Có, nếu không dùng `maTbs` | Một mã thuê bao cần gửi. |
| `maTbs` | string[] | Có, nếu không dùng `maTb` | Danh sách mã thuê bao cần gửi. Mã trùng sẽ được loại bỏ, không phân biệt hoa thường. |
| `reportPhone` | string | Không | Số điện thoại báo hỏng/số cần nhận tin cho lần gửi này. Chỉ dùng với `maTb` một mã; nếu gửi hàng loạt, dùng `recipients[].reportPhone`. Nếu có, hệ thống lookup UID bằng số này và lưu identity phụ theo cùng `maTb` + tài khoản gửi. |
| `content` | string | Có | Nội dung tin nhắn. Hỗ trợ biến template ở mục 5. |

### Response thành công hoặc thành công một phần

Nếu có ít nhất một tin gửi được, HTTP status là `200`.

```json
{
  "total": 3,
  "sent": 2,
  "failed": 1,
  "results": [
    {
      "maTb": "TB001",
      "success": true,
      "zaloAccountId": "zalo-account-1",
      "zaloUid": "9033407375370635391"
    },
    {
      "maTb": "TB002",
      "success": true,
      "zaloAccountId": "zalo-account-2",
      "zaloUid": "9022222222222222222"
    },
    {
      "maTb": "TB404",
      "success": false,
      "error": "contact_not_found"
    }
  ]
}
```

Nếu tất cả mã đều lỗi nghiệp vụ, HTTP status là `422` và body vẫn có dạng tổng hợp như trên. Nếu request sai cấu trúc, HTTP status là `400`.

### Các lỗi theo từng mã thuê bao

| `error` | Ý nghĩa | Cách xử lý |
| --- | --- | --- |
| `contact_not_found` | Không có contact thuộc tổ chức hiện tại với `maTb` này. | Import hoặc tạo contact đúng mã thuê bao. |
| `missing_zalo_sender_identity` | Contact chưa có `ContactMessagingIdentity` kênh Zalo. | Import dữ liệu mapping thuê bao - người nhận - tài khoản gửi. |
| `ambiguous_zalo_sender_identity` | Contact có nhiều identity Zalo nhưng không có identity chính. | Đánh dấu đúng một identity `isPrimary = true` cho mã thuê bao đó. |
| `missing_zalo_sender_account` | Mapping không có tài khoản Zalo gửi. | Bổ sung `senderZaloAccountId` cho identity. |
| `zalo_sender_account_not_connected` | Tài khoản Zalo gửi đang không ở trạng thái `connected`. | Kết nối lại tài khoản Zalo. |
| `missing_zalo_uid` | Không có Zalo UID người nhận, không có số điện thoại để lookup, hoặc SDK không hỗ trợ lookup. | Bổ sung số điện thoại hoặc `recipientExternalId`/`contact.zaloUid`. |
| `zalo_uid_lookup_not_found` | Lookup từ số điện thoại không tìm được Zalo UID. | Kiểm tra lại số điện thoại hoặc xử lý bằng luồng kết bạn/tra cứu thủ công. |
| `zalo_account_not_active` | Zalo account connected trong DB nhưng chưa active trong Zalo pool. | Đăng nhập/kết nối lại tài khoản Zalo trên hệ thống. |

### Ví dụ curl

```bash
curl -X POST "https://your-domain/api/public/messages/send-by-ma-tb" \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "maTb": "TB001",
    "content": "Xin chào {ho_ten}, mã thuê bao {ma_tb} tại {dia_chi} đã có lịch hỗ trợ."
  }'
```

## 3. Tạo chiến dịch gửi tin hàng loạt

Endpoint này tạo campaign và danh sách recipient để worker gửi tuần tự. Dùng endpoint này khi cần gửi số lượng lớn, có theo dõi trạng thái, tạm dừng hoặc tiếp tục.

Với campaign không truyền `zaloAccountId`, mỗi `maTb` dùng tài khoản Zalo trong `ContactMessagingIdentity.isPrimary = true`. Nếu recipient thiếu Zalo UID nhưng có `phone` và có tài khoản gửi tương ứng, recipient vẫn được đưa vào queue; worker sẽ lookup UID bằng đúng tài khoản đó tại thời điểm gửi, lưu UID vào danh bạ/identity, render lại nội dung rồi gửi.

```http
POST /api/public/bulk-messages/campaigns
```

### Request body

```json
{
  "name": "Thông báo sự cố",
  "externalRef": "APP-20260612-001",
  "content": "Kính chào {ho_ten}, mã thuê bao {ma_tb} tại {dia_chi} hiện đang gặp sự cố. VNPT sẽ hỗ trợ sớm.",
  "recipients": [
    { "maTb": "TB001", "reportPhone": "0987654321" },
    { "maTb": "TB002" },
    { "maTb": "TB003" }
  ],
  "options": {
    "dedupe": true,
    "skipMissingZaloUid": true,
    "delaySeconds": 8
  }
}
```

Có thể truyền `zaloAccountId` nếu muốn toàn bộ campaign gửi bằng một tài khoản Zalo cố định:

```json
{
  "zaloAccountId": "zalo-account-id",
  "name": "Thông báo bảo trì",
  "externalRef": "APP-20260612-002",
  "content": "Kính chào {ho_ten}, mã thuê bao {ma_tb} sẽ được bảo trì trong hôm nay.",
  "maTbs": ["TB001", "TB002"],
  "options": {
    "dedupe": true,
    "skipMissingZaloUid": true,
    "delaySeconds": 10
  }
}
```

Trường dữ liệu:

| Trường | Kiểu | Bắt buộc | Mặc định | Mô tả |
| --- | --- | --- | --- | --- |
| `zaloAccountId` | string | Không | Theo mapping từng thuê bao | Nếu truyền, hệ thống kiểm tra tài khoản thuộc tổ chức và đang `connected`. Nếu không truyền, mỗi recipient dùng `senderZaloAccountId` từ `ContactMessagingIdentity`. |
| `name` | string | Không | null | Tên chiến dịch để dễ tra cứu. |
| `externalRef` | string | Không | null | Mã tham chiếu của hệ thống ngoài. Nên truyền để đối soát. |
| `content` | string | Có | - | Mẫu nội dung tin nhắn. Hỗ trợ biến template ở mục 5. |
| `maTbs` | string[] | Có, nếu không dùng `recipients` | - | Danh sách mã thuê bao, tối đa 1000 phần tử/request. Dùng số danh bạ của từng contact. |
| `recipients` | object[] | Có, nếu không dùng `maTbs` | - | Danh sách phần tử `{ "maTb": "...", "reportPhone": "..." }`. `reportPhone` là tùy chọn cho từng thuê bao. |
| `options.dedupe` | boolean | Không | `true` | Loại trùng theo `maTb` và theo cặp tài khoản gửi + Zalo UID. |
| `options.skipMissingZaloUid` | boolean | Không | `true` | Nếu true, recipient thiếu Zalo UID bị đánh dấu `skipped`. |
| `options.delaySeconds` | number | Không | `8` | Khoảng nghỉ giữa hai tin. Giá trị được ép trong khoảng 3 đến 120 giây. |

### Response khi tạo campaign

HTTP status `201`.

```json
{
  "campaignId": "campaign-1",
  "status": "queued",
  "total": 3,
  "queued": 2,
  "skipped": 1,
  "skippedItems": [
    {
      "maTb": "TB003",
      "reason": "missing_zalo_uid"
    }
  ]
}
```

Nếu không có recipient nào đủ điều kiện gửi, campaign vẫn được tạo với `status: "completed"`, `queued: 0` và các recipient lỗi ở trạng thái `skipped`.

### Lỗi tạo campaign

| HTTP | Body | Nguyên nhân |
| --- | --- | --- |
| 400 | `{ "error": "content is required" }` | Thiếu hoặc rỗng `content`. |
| 400 | `{ "error": "maTbs or recipients must be a non-empty array" }` | Không truyền danh sách hợp lệ. |
| 400 | `{ "error": "recipients must contain at most 1000 items" }` | Danh sách vượt giới hạn 1000. |
| 400 | `{ "error": "recipients must contain strings or objects" }` | Phần tử danh sách không đúng kiểu. |
| 400 | `{ "error": "recipients.maTb must be a string" }` | Phần tử object thiếu `maTb` dạng string. |
| 400 | `{ "error": "recipients.reportPhone must be a string" }` | `reportPhone` không phải string. |
| 400 | `{ "error": "maTbs must not contain blank values" }` | Có mã thuê bao rỗng sau khi trim. |
| 404 | `{ "error": "Zalo account not found" }` | `zaloAccountId` không thuộc tổ chức của API key. |
| 422 | `{ "error": "Zalo account is not connected" }` | `zaloAccountId` không ở trạng thái connected. |

### Ví dụ curl

```bash
curl -X POST "https://your-domain/api/public/bulk-messages/campaigns" \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Thong bao su co",
    "externalRef": "APP-20260612-001",
    "content": "Kinh chao {ho_ten}, ma thue bao {ma_tb} tai {dia_chi} hien dang gap su co.",
    "maTbs": ["TB001", "TB002", "TB003"],
    "options": {
      "dedupe": true,
      "skipMissingZaloUid": true,
      "delaySeconds": 8
    }
  }'
```

## 4. Theo dõi và điều khiển campaign

### Xem trạng thái campaign

```http
GET /api/public/bulk-messages/campaigns/:id
```

Response:

```json
{
  "id": "campaign-1",
  "externalRef": "APP-20260612-001",
  "status": "running",
  "total": 100,
  "queued": 33,
  "sent": 60,
  "failed": 2,
  "skipped": 5,
  "createdAt": "2026-06-12T01:00:00.000Z",
  "startedAt": "2026-06-12T01:00:05.000Z",
  "completedAt": null
}
```

Campaign status:

| Status | Ý nghĩa |
| --- | --- |
| `queued` | Campaign đã tạo, chờ worker xử lý. |
| `running` | Worker đang gửi hoặc còn recipient trong hàng đợi. |
| `paused` | Campaign đang tạm dừng, worker không lấy để gửi. |
| `completed` | Không còn recipient queued/sending. |

### Xem danh sách recipient

```http
GET /api/public/bulk-messages/campaigns/:id/recipients?status=failed&limit=100
```

Query:

| Query | Kiểu | Mặc định | Mô tả |
| --- | --- | --- | --- |
| `status` | string | Không lọc | Lọc theo `queued`, `sending`, `sent`, `failed`, `skipped`. |
| `limit` | number | 100 | Giới hạn 1 đến 500. |

Response:

```json
{
  "recipients": [
    {
      "id": "recipient-1",
      "maTb": "TB001",
      "zaloUid": "9033407375370635391",
      "fullName": "Nguyen Van A",
      "phone": "0912345678",
      "address": "12 Pho Hue",
      "status": "sent",
      "error": null,
      "sentAt": "2026-06-12T01:01:00.000Z",
      "createdAt": "2026-06-12T01:00:00.000Z"
    }
  ]
}
```

Recipient status:

| Status | Ý nghĩa |
| --- | --- |
| `queued` | Đủ điều kiện gửi, đang chờ đến lượt. |
| `sending` | Worker đang xử lý recipient này. |
| `sent` | Đã gửi thành công và đã ghi vào hội thoại. |
| `failed` | Gửi lỗi trong worker, xem trường `error`. |
| `skipped` | Bị bỏ qua ngay khi tạo campaign vì thiếu dữ liệu hoặc trùng. |

### Tạm dừng campaign

```http
POST /api/public/bulk-messages/campaigns/:id/pause
```

Chỉ campaign `queued` hoặc `running` mới tạm dừng được.

Response:

```json
{
  "id": "campaign-1",
  "status": "paused"
}
```

### Tiếp tục campaign

```http
POST /api/public/bulk-messages/campaigns/:id/resume
```

Chỉ campaign `paused` mới tiếp tục được.

Response:

```json
{
  "id": "campaign-1",
  "status": "queued"
}
```

## 5. Biến template nội dung

Các API theo `maTb` và bulk campaign cùng dùng chung cơ chế render template.

| Biến | Dữ liệu thay thế | Fallback |
| --- | --- | --- |
| `{ho_ten}` | `contact.fullName` | `quý khách` |
| `{ten}` | `contact.fullName` | `quý khách` |
| `{name}` | `contact.fullName` | `quý khách` |
| `{ma_tb}` | Mã thuê bao trong request | Bắt buộc, lỗi nếu rỗng |
| `{dia_chi}` | `contact.address` | `địa chỉ thuê bao` |
| `{address}` | `contact.address` | `địa chỉ thuê bao` |
| `{dien_thoai}` | `contact.phone` | Chuỗi rỗng |
| `{phone}` | `contact.phone` | Chuỗi rỗng |
| `{zalo_uid}` | Zalo UID người nhận | Chuỗi rỗng |

Biến không nằm trong danh sách trên sẽ được giữ nguyên.

Ví dụ:

```text
Kính chào {ho_ten}, mã thuê bao {ma_tb} tại {dia_chi} cần hỗ trợ.
```

Nếu contact có `fullName = "Nguyen Van A"`, `maTb = "TB001"`, `address = "12 Pho Hue"`, nội dung gửi đi là:

```text
Kính chào Nguyen Van A, mã thuê bao TB001 tại 12 Pho Hue cần hỗ trợ.
```

## 6. Điều kiện dữ liệu để gửi đúng

Để gửi theo mã thuê bao ổn định, mỗi contact nên có:

| Dữ liệu | Bắt buộc cho gửi ngay theo `maTb` | Bắt buộc cho bulk không truyền `zaloAccountId` | Ghi chú |
| --- | --- | --- | --- |
| `Contact.maTb` | Có | Có | Dùng để tìm contact. So khớp không phân biệt hoa thường. |
| `ContactMessagingIdentity.channel = "zalo"` | Có | Có | Identity chính được ưu tiên qua `isPrimary`. |
| `ContactMessagingIdentity.recipientExternalId` hoặc `Contact.zaloUid` | Không nếu có `phone` để lookup | Không nếu có `phone` để lookup | Đây là Zalo UID người nhận. Nếu thiếu, hệ thống lookup bằng số điện thoại và lưu lại khi tìm được. |
| `ContactMessagingIdentity.senderZaloAccountId` | Có | Có nếu không truyền `zaloAccountId` | Tài khoản Zalo dùng để gửi. |
| `ZaloAccount.status = "connected"` | Có | Có | Tài khoản cũng phải active trong Zalo pool khi worker gửi. |

Khi tạo campaign có truyền `zaloAccountId`, hệ thống dùng tài khoản này cho toàn bộ danh sách. Khi không truyền, hệ thống chọn tài khoản gửi theo `ContactMessagingIdentity` của từng mã thuê bao.

Với luồng gửi theo `maTb`, nên đảm bảo mỗi contact chỉ có một identity chính (`isPrimary = true`) cho tài khoản gửi chuẩn. Lookup UID, cập nhật danh bạ và gửi tin phải dùng cùng `senderZaloAccountId` đó để tránh lẫn tài khoản.

Nếu request có `reportPhone`, hệ thống không ghi đè `Contact.phone` và không ghi đè `Contact.zaloUid` của số danh bạ. UID lookup được sẽ được lưu vào `ContactMessagingIdentity` riêng với `source = "incident_report"`, `recipientPhone = reportPhone`, cùng `maTb` và cùng tài khoản gửi. Các lần gửi sau với cùng `maTb` + `reportPhone` + tài khoản gửi sẽ dùng lại UID này, không lookup lại. Hội thoại/message vẫn gắn về cùng contact thuê bao, nhưng `externalThreadId` là Zalo UID của số nhận thực tế.

## 7. API gửi trực tiếp theo threadId

Endpoint này gửi ngay vào một thread Zalo đã biết. Dùng khi hệ thống ngoài đã có `zaloAccountId` và `threadId`, không cần tìm theo mã thuê bao.

```http
POST /api/public/messages/send
```

Request:

```json
{
  "zaloAccountId": "zalo-account-id",
  "threadId": "9033407375370635391",
  "threadType": "user",
  "content": {
    "msg": "Xin chào!"
  }
}
```

Trường dữ liệu:

| Trường | Kiểu | Bắt buộc | Mô tả |
| --- | --- | --- | --- |
| `zaloAccountId` | string | Có | Tài khoản Zalo gửi. Phải thuộc tổ chức và connected. |
| `threadId` | string | Có | Zalo UID người nhận hoặc ID nhóm. |
| `threadType` | string | Không | Truyền `"group"` để gửi nhóm, giá trị khác hoặc bỏ trống là chat cá nhân. |
| `content` | any | Có | Payload truyền thẳng vào Zalo SDK. Với text nên dùng `{ "msg": "Nội dung" }`. |

Response:

```json
{ "success": true }
```

Lưu ý: endpoint này không render template và không ghi message vào CRM như API theo `maTb` hoặc bulk worker.

## 8. Webhook liên quan gửi hàng loạt

Nếu tổ chức cấu hình Webhook URL, ZaloCRM sẽ gửi webhook dạng:

```json
{
  "event": "bulk.message.sent",
  "timestamp": "2026-06-12T01:01:00.000Z",
  "data": {
    "campaignId": "campaign-1",
    "externalRef": "APP-20260612-001",
    "maTb": "TB001",
    "zaloUid": "9033407375370635391",
    "sentAt": "2026-06-12T01:01:00.000Z"
  }
}
```

Header webhook:

```http
Content-Type: application/json
X-Webhook-Event: bulk.message.sent
X-Webhook-Signature: hmac-sha256-hex
```

Sự kiện:

| Event | Khi nào phát sinh |
| --- | --- |
| `bulk.campaign.created` | Tạo campaign thành công. |
| `bulk.message.sent` | Một recipient gửi thành công. |
| `bulk.message.failed` | Một recipient gửi lỗi. |
| `bulk.campaign.completed` | Campaign không còn recipient queued/sending. |

Nếu cấu hình `webhook_secret`, `X-Webhook-Signature` là HMAC-SHA256 của raw JSON body bằng secret đó.

## 9. Khuyến nghị tích hợp

1. Dùng `POST /api/public/messages/send-by-ma-tb` cho các tin đơn lẻ hoặc danh sách rất nhỏ cần gửi ngay.
2. Dùng `POST /api/public/bulk-messages/campaigns` cho gửi hàng loạt vì có queue, delay, trạng thái và webhook đối soát.
3. Luôn truyền `externalRef` khi tạo campaign để nối dữ liệu giữa hệ thống ngoài và ZaloCRM.
4. Đặt `delaySeconds` tối thiểu 8 giây nếu gửi số lượng lớn để giảm rủi ro giới hạn từ Zalo.
5. Sau khi tạo campaign, gọi endpoint status theo chu kỳ hoặc nhận webhook để cập nhật kết quả.
6. Nếu nhận `skippedItems`, xử lý lại dữ liệu contact/mapping trước khi gửi lại.
7. Với danh sách thiếu UID, nên import đủ `phone` và `senderZaloAccountId`; hệ thống sẽ tự lookup UID một lần và tái sử dụng ở các lần gửi sau.
