import "./v2.css";
import { Shell } from "@/v2/ui";
import { connection } from "next/server";
export default async function V2Layout({children}:{children:React.ReactNode}){
  // Request-time rendering while allowing independent public fetch revalidation.
  await connection();
  return <Shell>{children}</Shell>;
}