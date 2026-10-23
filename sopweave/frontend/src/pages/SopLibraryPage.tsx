import {useLocation,useNavigate} from 'react-router-dom';
import PageHeader from '../components/ui/PageHeader';
import SkillConfigPanel from '../components/SkillConfigPanel';
import {useAiRegistry} from '../context/AiRegistryContext';

export default function SopLibraryPage() {
  const {models}=useAiRegistry();
  const location=useLocation(), navigate=useNavigate();
  const back=new URLSearchParams(location.search).get('return');
  return <main className="app-page flex min-h-0 flex-1 flex-col">
    <PageHeader title="财务 SOP" description="查看内置标准流程，创建和管理你的 SOP。" actions={back?.startsWith('/projects/') && <button className="ui-button" onClick={() => navigate(back)}>返回任务</button>}/>
    <SkillConfigPanel models={models}/>
  </main>;
}
