# GDrive Transfer with Rclone and Google Colab

Một công cụ giúp chuyển dữ liệu giữa các dịch vụ lưu trữ đám mây (Google Drive, OneDrive, Dropbox, v.v.) bằng cách tận dụng băng thông tốc độ cao và dung lượng đĩa của Google Colab thông qua giao diện Web-UI thân thiện.

## Tính năng chính
- **Giao diện Web-UI trực quan:** Quản lý tệp tin (File Manager) cho cả nguồn (Source) và đích (Destination).
- **Hỗ trợ Rclone:** Tải lên file `rclone.conf` trực tiếp từ giao diện để cấu hình các kết nối đám mây.
- **Theo dõi tiến trình thời gian thực:** Hiển thị tốc độ truyền tải, thời gian dự kiến hoàn thành (ETA), dung lượng đã chuyển và danh sách các file đang được truyền tải.
- **Bảo mật:** Xác thực bằng token ngẫu nhiên được tạo mỗi lần chạy.
- **Expose qua Cloudflared:** Tự động tạo đường link public an toàn để truy cập Web-UI từ xa.

## Hướng dẫn sử dụng trên Google Colab
1. Mở file `ggtransfer.ipynb` trong Google Colab.
2. Chạy cell **Install Dependencies & Setup Rclone** để cài đặt các công cụ cần thiết.
3. Chạy cell **Run Web-UI & Expose with Cloudflared**.
4. Đợi vài giây, một đường link dạng `https://xxxx.trycloudflare.com/?token=xxxx` sẽ xuất hiện ở output của cell.
5. Click vào link để truy cập giao diện Web-UI và bắt đầu chuyển dữ liệu.

## Cấu trúc thư mục dự án
- `ggtransfer.ipynb`: File Jupyter Notebook chạy trên Google Colab.
- `src/main.py`: Backend FastAPI xử lý các API điều khiển rclone.
- `src/static/index.html`: Frontend Web-UI quản lý và hiển thị tiến trình.
- `rclone.conf`: File cấu hình rclone (sẽ được tạo/ghi đè khi người dùng upload).

