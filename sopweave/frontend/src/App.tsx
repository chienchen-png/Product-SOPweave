import { lazy, Suspense, useEffect, useState } from 'react';
import { BrowserRouter, useLocation, useNavigate } from 'react-router-dom';
import { FolderOpen } from 'lucide-react';
import NavSidebar from './layout/NavSidebar';
import NewProjectDialog from './components/NewProjectDialog';
import { listProjects, createProject, type Project } from './lib/api';
import { readRoute, viewPath, type View } from './lib/navigation';
import { useAiRegistry } from './context/AiRegistryContext';
import {FeedbackProvider} from './components/ui/Feedback';
import PageBoundary from './components/PageBoundary';

const ChatPage = lazy(() => import('./pages/ChatPage'));
const AiConfigPage = lazy(() => import('./pages/AiConfigPage'));
const DataCheckAppPage = lazy(() => import('./pages/DataCheckAppPage'));
const FinModAppPage = lazy(() => import('./pages/FinModAppPage'));
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const PreviewPage = lazy(() => import('./pages/PreviewPage'));
const SopLibraryPage = lazy(() => import('./pages/SopLibraryPage'));

export default function App() {
  return <BrowserRouter><FeedbackProvider><Workbench /></FeedbackProvider></BrowserRouter>;
}

function Workbench() {
  const location = useLocation(), navigate = useNavigate();
  const { view, projectId } = readRoute(location.pathname);
  const query = new URLSearchParams(location.search);
  const preview = query.get('preview') === '1' && query.get('project_id') && query.get('path');
  const [projects, setProjects] = useState<Project[]>([]);
  const [lastProject, setLastProject] = useState('');
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectError, setProjectError] = useState('');
  const [showNew, setShowNew] = useState(false);
  const { models, agents, providers, loading, error: registryError, refresh } = useAiRegistry();
  const activeProject = projects.find(p => p.id === (projectId || lastProject)) || null;
  const defaultModel = models.find(m => m.is_default)?.id || models[0]?.id || '';
  const defaultAgentId = agents.find(agent => Boolean(agent.enabled))?.id || '';

  useEffect(() => {
    if (location.pathname.startsWith('/settings/') && !['/settings/models', '/settings/agent'].includes(location.pathname)) {
      navigate('/settings/models', { replace: true });
    }
  }, [location.pathname, navigate]);

  useEffect(() => {
    let cancelled = false;
    listProjects().then(list => {
      if (cancelled) return;
      const visible = list.filter(p => !p.name.startsWith('__app_'));
      setProjects(visible);
      setLastProject(current => current || visible[0]?.id || '');
    }).catch(cause => { if (!cancelled) setProjectError(cause.message || '项目加载失败'); })
      .finally(() => { if (!cancelled) setProjectsLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const selectProject = (id: string) => { setLastProject(id); navigate(viewPath('chat', id)); };
  const go = (next: View) => navigate(viewPath(next, activeProject?.id || lastProject));
  const settingsTab = location.pathname === '/settings/agent' ? 'agent' : 'models';

  async function handleNewProject(name: string, workDir: string, model: string) {
    const project = await createProject({ name, work_dir: workDir, default_model: model || undefined });
    setProjects(current => [...current, project]);
    setShowNew(false);
    selectProject(project.id);
  }

  if (preview) return <Suspense fallback={<p className="p-4">加载预览…</p>}>
    <PreviewPage projectId={query.get('project_id')!} filePath={query.get('path')!} />
  </Suspense>;

  return <div className="flex h-full overflow-hidden">
    <NavSidebar projects={projects} activeProjectId={activeProject?.id || null} activeView={view}
      onSelectProject={selectProject} onNewProject={() => setShowNew(true)} onNavigate={go}
      onDeleteProject={id => {
        setProjects(current => current.filter(p => p.id !== id));
        if (activeProject?.id === id) { setLastProject(''); navigate('/projects'); }
      }}
      onDuplicateProject={project => setProjects(current => [...current, project])}
      onRenameProject={(id, name) => setProjects(current => current.map(p => p.id === id ? { ...p, name } : p))} />
    <div className="flex min-w-0 flex-1 flex-col">
      {registryError && <div role="alert" className="flex items-center gap-3 border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm">
        <span>模型配置暂不可用：{registryError}</span><button onClick={() => void refresh().catch(() => {})}>重试</button>
      </div>}
      {projectError && <p role="alert" className="bg-red-50 px-4 py-2 text-sm text-red-700">{projectError}</p>}
      <PageBoundary key={location.pathname}><Suspense fallback={<p role="status" className="p-6">加载页面…</p>}>
        {view === 'ai-config' && <AiConfigPage tab={settingsTab} onTabChange={tab => navigate(`/settings/${tab}`)} />}
        {view === 'sops' && <SopLibraryPage/>}
        {view === 'dashboard' && <DashboardPage />}
        {view === 'app-sales-check' && <DataCheckAppPage models={models} providers={providers} defaultModel={defaultModel} />}
        {view === 'app-finmod' && <FinModAppPage models={models} providers={providers} defaultModel={defaultModel} />}
        {view === 'chat' && projectsLoading && <p role="status" className="p-6">加载项目…</p>}
        {view === 'chat' && !projectsLoading && activeProject && <ChatPage key={activeProject.id} project={activeProject}
          initialSkillId={query.get('skill')||undefined} onSkillSelected={()=>{const next=new URLSearchParams(location.search);next.delete('skill');navigate(location.pathname+(next.size?'?'+next:''),{replace:true});}} initialPanel={query.get('panel')==='files'?'files':undefined} resumeRunId={query.get('run')||undefined} onManageSops={() => navigate(`/sops?return=${encodeURIComponent(location.pathname)}`)}
          models={models} agentId={defaultAgentId} providers={providers} defaultModel={activeProject.default_model || defaultModel}
          onModelChange={id => setProjects(current => current.map(p => p.id === activeProject.id ? { ...p, default_model: id } : p))} />}
        {view === 'chat' && !projectsLoading && !activeProject && <div className="flex flex-1 flex-col items-center justify-center gap-4 p-6">
          <FolderOpen size={32} className="text-zinc-400" /><h1 className="text-lg font-semibold">{projectId ? '找不到该项目' : '还没有打开项目'}</h1>
          <p className="text-sm text-zinc-500">{projectId ? '项目可能已删除，或属于专项应用工作区。' : '选择已有项目，或创建一个项目开始工作。'}</p>
          <button className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white" onClick={() => setShowNew(true)}>新建项目</button>
        </div>}
      </Suspense></PageBoundary>
      {loading && <div role="status" className="fixed bottom-2 right-3 rounded bg-white px-3 py-1 text-xs text-zinc-500 shadow">正在加载模型配置…</div>}
    </div>
    {showNew && <NewProjectDialog onCancel={() => setShowNew(false)} onCreate={handleNewProject} />}
  </div>;
}
