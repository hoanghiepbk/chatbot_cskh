import { useLogin } from "@refinedev/core";
import { Button, Card, Form, Input, Typography } from "antd";
import { useState } from "react";
import { SEMANTIC } from "../theme";

// Minimal login: paste STAFF_API_TOKEN → validated → stored in sessionStorage.
export function Login() {
  const { mutate: login } = useLogin<{ token: string }>();
  const [loading, setLoading] = useState(false);

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        background: SEMANTIC.bg,
      }}
    >
      <Card style={{ width: 400 }} styles={{ body: { padding: 28 } }}>
        <Typography.Title level={4} style={{ marginTop: 0, marginBottom: 4 }}>
          XeCare Console
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 20 }}>
          Công cụ vận hành nội bộ. Nhập STAFF_API_TOKEN để tiếp tục.
        </Typography.Paragraph>
        <Form
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => {
            setLoading(true);
            login(values, { onSettled: () => setLoading(false) });
          }}
        >
          <Form.Item
            label="Staff API token"
            name="token"
            rules={[{ required: true, message: "Vui lòng nhập token" }]}
          >
            <Input.Password placeholder="STAFF_API_TOKEN" autoFocus />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={loading}>
            Đăng nhập
          </Button>
        </Form>
        <Typography.Paragraph
          type="secondary"
          style={{ fontSize: 12, marginTop: 16, marginBottom: 0 }}
        >
          Token lưu trong sessionStorage (mất khi đóng tab), không ghi xuống đĩa. Demo-grade:
          bearer dùng chung, chưa có danh tính từng nhân viên.
        </Typography.Paragraph>
      </Card>
    </div>
  );
}
