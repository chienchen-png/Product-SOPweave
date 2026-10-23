/** SOP library: read-only built-ins and versioned user SOPs. */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { BookOpen, ChevronDown, RefreshCw, UserRound } from "lucide-react";
import {
  fetchSkills,
  fetchSkillDetail,
  createSkillDraft,
  deleteUserSkill,
  type SkillSummary,
  type SkillDetail,
  type ModelOption,
} from "../lib/api";
import { notifySkillsChanged, subscribeSkillsChanged } from "../lib/skillEvents";
import BuiltinSkillDetail from "./skills/BuiltinSkillDetail";
import UserSkillDetail from "./skills/UserSkillDetail";
import SkillCreateWizard from "./skills/SkillCreateWizard";
import SkillImportDialog from "./skills/SkillImportDialog";
import SkillDraftList from "./skills/SkillDraftList";

type SopScope = "builtin" | "user";
type UserSopTab = "library" | "drafts";

function TabButton({active,icon,label,onClick,disabled}:{
  active:boolean;
  icon:ReactNode;
  label:string;
  onClick:()=>void;
  disabled?:boolean;
}) {
  return <button
    type="button"
    disabled={disabled}
    onClick={onClick}
    className={`flex h-9 items-center gap-1.5 border-b-2 px-2.5 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
      active ? "border-blue-600 text-blue-700" : "border-transparent text-zinc-500 hover:text-zinc-800"
    }`}
  >
    {icon}
    {label}
  </button>;
}

export default function SkillConfigPanel({models=[]}:{models?:ModelOption[]}) {
  const [importOpen,setImportOpen] = useState(false);
  const [createMenuOpen,setCreateMenuOpen] = useState(false);
  const [skills,setSkills] = useState<SkillSummary[]>([]);
  const [searchParams,setSearchParams] = useSearchParams();
  const [scope,setScope] = useState<SopScope>("builtin");
  const [userSopTab,setUserSopTab] = useState<UserSopTab>("library");
  const [detail,setDetail] = useState<SkillDetail|null>(null);
  const [error,setError] = useState("");
  const [loading,setLoading] = useState(true);
  const [userSkillsEnabled,setUserSkillsEnabled] = useState(true);
  const [busy,setBusy] = useState(false);
  const [query,setQuery] = useState("");
  const [cursor,setCursor] = useState("");
  const [next,setNext] = useState("");
  const [generation,setGeneration] = useState(0);
  const [reloadKey,setReloadKey] = useState(0);
  const [wizard,setWizard] = useState<string|null|undefined>(undefined);
  const [confirmDelete,setConfirmDelete] = useState(false);
  const [draftGeneration,setDraftGeneration] = useState(0);
  const requestId = useRef(0);

  const builtinSkills = skills.filter(skill => (skill.origin || "builtin") === "builtin" && skill.name !== "office-files");
  const officeSkills = skills.filter(skill => skill.name === "office-files");
  const userSkills = skills.filter(skill => skill.origin === "user");
  const visibleSkills = scope === "builtin" ? [...builtinSkills,...officeSkills] : userSkills;
  const requestedSkill = searchParams.get("skill") || "";
  const selected = visibleSkills.some(skill => skill.name === requestedSkill)
    ? requestedSkill
    : visibleSkills[0]?.name || "";

  function setSelected(name:string,replace=false) {
    setSearchParams(current => {
      const next = new URLSearchParams(current);
      if (name) next.set("skill",name);
      else next.delete("skill");
      return next;
    },{replace});
  }

  const load = useCallback(async () => {
    const id = ++requestId.current;
    try {
      const page = await fetchSkills({query,cursor,includeInternal:true});
      if (id !== requestId.current) return;
      setSkills(page.skills);
      setNext(page.next_cursor || "");
      setUserSkillsEnabled(page.user_skills_enabled !== false);
      setGeneration(value => value + 1);
      setError("");
    } catch (cause) {
      if (id === requestId.current) setError((cause as Error).message);
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  },[query,cursor,reloadKey]);

  useEffect(() => {
    const timer = setTimeout(() => void load(),150);
    return () => {
      clearTimeout(timer);
      requestId.current++;
    };
  },[load]);
  useEffect(() => subscribeSkillsChanged(() => void load()),[load]);
  useEffect(() => {
    const item = skills.find(skill => skill.name === requestedSkill);
    if (item?.origin === "user") setScope("user");
  },[requestedSkill,skills]);
  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    let disposed = false;
    setConfirmDelete(false);
    setDetail(null);
    fetchSkillDetail(selected).then(value => {
      if (!disposed) setDetail(value);
    }).catch(cause => {
      if (!disposed) setError((cause as Error).message);
    });
    return () => {
      disposed = true;
    };
  },[selected,generation]);

  function switchScope(nextScope:SopScope) {
    setScope(nextScope);
    setCreateMenuOpen(false);
    setQuery("");
    setCursor("");
    setSelected("",true);
    if (nextScope === "user") setUserSopTab("library");
  }

  async function edit() {
    if (!detail) return;
    setBusy(true);
    setError("");
    try {
      const draft = await createSkillDraft({
        base_skill_id:detail.name,
        base_version:detail.version,
      });
      setWizard(draft.draft_id);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <>
    <div className="flex gap-1 border-b border-zinc-200 bg-white px-6">
      <TabButton active={scope==="builtin"} icon={<BookOpen size={13}/>} label="内置 SOP" onClick={()=>switchScope("builtin")}/>
      <TabButton active={scope==="user"} icon={<UserRound size={13}/>} label="用户 SOP" onClick={()=>switchScope("user")} disabled={!userSkillsEnabled}/>
      <div className="ml-auto flex items-center gap-1 pb-2 pt-1.5">
        <button
          type="button"
          title="刷新"
          disabled={loading}
          onClick={()=>{setCursor("");setReloadKey(value=>value+1);}}
          className="rounded p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 disabled:opacity-40"
        >
          <RefreshCw size={14}/>
        </button>
      </div>
    </div>

    <div className="page-body min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-6xl space-y-4">
        {error && <p role="alert" className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>}
        {!userSkillsEnabled && scope === "user" && <p className="text-sm text-zinc-500">用户 SOP 库暂未启用。</p>}

        {scope === "user" && (
          <div className="flex flex-wrap items-center gap-2">
            <div role="group" aria-label="用户 SOP 分类" className="ui-tabs">
              <button aria-pressed={userSopTab==="library"} className="ui-tab" onClick={()=>setUserSopTab("library")}>已发布</button>
              <button aria-pressed={userSopTab==="drafts"} className="ui-tab" onClick={()=>setUserSopTab("drafts")}>草稿</button>
            </div>
            <div className="relative ml-auto">
              <button
                type="button"
                aria-label="新增 SOP"
                aria-haspopup="menu"
                aria-expanded={createMenuOpen}
                onClick={()=>setCreateMenuOpen(open=>!open)}
                className="ui-button ui-button-primary flex items-center gap-1.5"
              >
                新增 SOP
                <ChevronDown size={12} className={createMenuOpen?"rotate-180 transition-transform":"transition-transform"}/>
              </button>
              {createMenuOpen && <>
                <div aria-hidden="true" className="fixed inset-0 z-20" onClick={()=>setCreateMenuOpen(false)}/>
                <div role="menu" className="absolute right-0 top-full z-30 mt-1 w-48 overflow-hidden rounded-lg border border-zinc-200 bg-white p-1 shadow-lg">
                  <button
                    type="button"
                    role="menuitem"
                    className="block w-full rounded-md px-2.5 py-2 text-left text-sm text-zinc-700 hover:bg-zinc-50"
                    onClick={()=>{setCreateMenuOpen(false);setWizard(null);}}
                  >
                    新建 SOP
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="block w-full rounded-md px-2.5 py-2 text-left text-sm text-zinc-700 hover:bg-zinc-50"
                    onClick={()=>{setCreateMenuOpen(false);setImportOpen(true);}}
                  >
                    导入 GitHub / ZIP
                  </button>
                </div>
              </>}
            </div>
          </div>
        )}

        {scope === "user" && userSopTab === "drafts" ? (
          <SkillDraftList generation={draftGeneration} onOpen={setWizard}/>
        ) : (
          <div className="flex flex-col gap-5 lg:flex-row">
            <aside className="w-full shrink-0 space-y-3 lg:w-52">
              <input
                aria-label={scope === "builtin" ? "搜索内置 SOP" : "搜索用户 SOP"}
                placeholder="搜索名称或功能"
                className="w-full rounded-lg border bg-white px-3 py-2.5 text-sm"
                value={query}
                onChange={event=>{setCursor("");setQuery(event.target.value);}}
              />
              {loading && <p className="text-sm text-zinc-500">加载中…</p>}
              {scope === "builtin" ? <>
                <section>
                  <h3 className="mb-2 text-xs font-semibold text-zinc-500">内置 SOP</h3>
                  <nav className="space-y-1">
                    {builtinSkills.map(skill => <button
                      key={skill.name}
                      className={`block w-full rounded-md p-2 text-left text-sm ${selected===skill.name?"bg-white text-zinc-900 shadow-sm ring-1 ring-zinc-200":"text-zinc-600 hover:bg-zinc-100"}`}
                      onClick={()=>setSelected(skill.name)}
                    >
                      <span className="block truncate">{skill.title || skill.name}</span>
                      <span className="text-xs text-zinc-400">{skill.step_count ? `${skill.step_count} 步` : "指引"}</span>
                    </button>)}
                  </nav>
                </section>
                {!!officeSkills.length && <section>
                  <h3 className="mb-2 text-xs font-semibold text-zinc-500">Office 支撑</h3>
                  <nav className="space-y-1">
                    {officeSkills.map(skill => <button
                      key={skill.name}
                      className={`block w-full rounded-md p-2 text-left text-sm ${selected===skill.name?"bg-white text-zinc-900 shadow-sm ring-1 ring-zinc-200":"text-zinc-600 hover:bg-zinc-100"}`}
                      onClick={()=>setSelected(skill.name)}
                    >
                      <span className="block truncate">{skill.title || skill.name}</span>
                      <span className="text-xs text-zinc-400">工具与文件规则</span>
                    </button>)}
                  </nav>
                </section>}
              </> : <section>
                <h3 className="mb-2 text-xs font-semibold text-zinc-500">我的 SOP</h3>
                <nav className="space-y-1">
                  {userSkills.map(skill => <button
                    key={skill.name}
                    className={`block w-full rounded-md p-2 text-left text-sm ${selected===skill.name?"bg-white text-zinc-900 shadow-sm ring-1 ring-zinc-200":"text-zinc-600 hover:bg-zinc-100"}`}
                    onClick={()=>setSelected(skill.name)}
                  >
                    <span className="block truncate">{skill.title || skill.name}</span>
                    <span className="text-xs text-zinc-400">{skill.step_count ? `${skill.step_count} 步` : "指引"}{skill.version ? ` · v${skill.version}` : ""}</span>
                  </button>)}
                  {!loading && !userSkills.length && <p className="px-2 py-3 text-xs text-zinc-400">还没有已发布的用户 SOP。</p>}
                </nav>
              </section>}
              <div className="flex gap-2 text-xs">
                <button disabled={!cursor} onClick={()=>setCursor("")}>首页</button>
                <button disabled={!next} onClick={()=>setCursor(next)}>下一页</button>
              </div>
            </aside>

            <div className="min-w-0 flex-1 space-y-3">
              {scope === "user" && detail?.origin === "user" && (
                <div className="flex justify-end gap-2">
                  <button className="ui-button" disabled={busy} onClick={()=>void edit()}>编辑 SOP</button>
                  <button className="ui-button text-red-700" onClick={()=>setConfirmDelete(true)}>删除 SOP</button>
                </div>
              )}
              {scope === "user" && confirmDelete && detail?.origin === "user" && (
                <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm">
                  <p>删除后智能体将无法发现或加载此 SOP。正在使用的任务会阻止删除。</p>
                  <button
                    className="mr-3 mt-2 text-red-700"
                    disabled={busy}
                    onClick={()=>{
                      setBusy(true);
                      void deleteUserSkill(detail.name,detail.version!).then(()=>{
                        setSkills(current=>current.filter(skill=>skill.name!==detail.name));
                        setSelected("",true);
                        setDetail(null);
                        setConfirmDelete(false);
                        notifySkillsChanged();
                        void load();
                      }).catch(cause=>setError(cause.message)).finally(()=>setBusy(false));
                    }}
                  >
                    确认删除
                  </button>
                  <button onClick={()=>setConfirmDelete(false)}>取消</button>
                </div>
              )}
              {detail ? detail.origin === "user" ? <UserSkillDetail detail={detail}/> : <BuiltinSkillDetail detail={detail}/> :
                <p className="p-5 text-sm text-zinc-400">选择流程查看功能、工具和执行步骤。</p>}
            </div>
          </div>
        )}
      </div>
    </div>

    {importOpen && <SkillImportDialog onClose={()=>setImportOpen(false)} onPrepared={id=>{setImportOpen(false);setWizard(id);}}/>}
    {wizard !== undefined && <SkillCreateWizard
      draftId={wizard || undefined}
      models={models}
      onClose={()=>{setWizard(undefined);setDraftGeneration(value=>value+1);}}
      onPublished={ref=>{
        setWizard(undefined);
        setDraftGeneration(value=>value+1);
        setScope("user");
        setUserSopTab("library");
        setSelected(ref.skill_id);
        setCursor("");
        void load();
      }}
    />}
  </>;
}
