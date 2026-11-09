import KubernetesView from "./KubernetesView";

const NAME = /^[a-z][a-z0-9-]{0,31}$/;
const PSEUDONYM = /^[a-z][a-z0-9]{1,20}$/;

export default async function Home({ searchParams }) {
  const params = await searchParams;
  const classId = typeof params?.class === "string" && NAME.test(params.class) ? params.class : null;
  const student = typeof params?.student === "string" && PSEUDONYM.test(params.student) ? params.student : null;
  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 640, margin: "4rem auto", padding: "0 1rem" }}>
      <h1>Homeroom</h1>
      <p>To learn the pod, you must become the pod.</p>
      <p>Coming soon: pick your state and the courses you&apos;ve taken, and see which KubeCon talks you&apos;re ready for.</p>
      {classId && student && <KubernetesView classId={classId} student={student} />}
    </main>
  );
}
