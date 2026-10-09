import { Suspense } from "react";

import CandidateView from "@/components/CandidateView";
import { Loading } from "@/components/ui";

// cacheComponents: the client view reads the dynamic [id] param, so it must sit inside a Suspense boundary.
export default function CandidatePage() {
  return (
    <Suspense fallback={<Loading />}>
      <CandidateView />
    </Suspense>
  );
}
