import "./v2.css";
import { Shell } from "@/v2/ui";
export const dynamic = "force-dynamic";
export default function V2Layout({children}:{children:React.ReactNode}){
  return <Shell>{children}</Shell>;
}