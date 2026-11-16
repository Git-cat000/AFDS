import { api } from "./api";
import type { ProductionStage, SkillPackage } from "./skill-types";

function readFile(file: File): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(new Error("无法读取所选技能文件"));
    reader.readAsArrayBuffer(file);
  });
}

export async function importSkillFiles(
  files: File[],
  stages: ProductionStage[],
  source: string,
) {
  if (!files.length || !stages.length)
    throw new Error("选择技能文件及至少一个适用阶段");
  if (files.length === 1 && files[0].name.toLowerCase().endsWith(".zip")) {
    if (files[0].size > 16 * 1024 ** 2) throw new Error("ZIP 最大16MiB");
    const query = new URLSearchParams({ stages: stages.join(","), source });
    const response = await fetch(`/api/v1/skills/import-zip?${query}`, {
      method: "POST",
      headers: { "Content-Type": "application/zip" },
      body: files[0],
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || "技能导入失败");
    return data as SkillPackage;
  }
  if (
    files.length > 256 ||
    files.some((file) => file.size > 8 * 1024 ** 2) ||
    files.reduce((sum, file) => sum + file.size, 0) > 32 * 1024 ** 2
  )
    throw new Error("技能包最多256个文件，单文件8MiB，总计32MiB");
  const resources = [];
  for (const file of files) {
    const bytes = new Uint8Array(await readFile(file));
    let value = "";
    for (let index = 0; index < bytes.length; index += 8192)
      value += String.fromCharCode(...bytes.subarray(index, index + 8192));
    resources.push({
      path:
        files.length === 1 ? "SKILL.md" : file.webkitRelativePath || file.name,
      content_base64: btoa(value),
    });
  }
  return api<SkillPackage>("/skills/import", {
    files: resources,
    stages,
    source,
  });
}
