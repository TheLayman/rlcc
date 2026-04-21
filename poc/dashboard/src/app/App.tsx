import { Toaster } from "@/app/components/ui/sonner";
import { Dashboard } from "@/app/components/dashboard";

export default function App() {
  return (
    <>
      <Dashboard />
      <Toaster theme="light" />
    </>
  );
}