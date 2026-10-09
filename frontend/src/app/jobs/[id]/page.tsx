import { Suspense } from "react";

import JobView from "@/components/JobView";
import { Loading } from "@/components/ui";

// cacheComponents: the client view reads the dynamic [id] param, so it must sit inside a Suspense boundary.
export default function JobPage() {
  return (
    <Suspense fallback={<Loading />}>
      <JobView />
    </Suspense>
  );
}
